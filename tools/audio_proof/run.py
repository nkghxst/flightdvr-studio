# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.

"""Synthetic proof CLI. Captures files only; never opens a hardware stream."""
import argparse
from array import array
from fractions import Fraction
import hashlib
from importlib import metadata
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import wave

from .model import AudioPlan, Span, Timeline
from .stream import BLOCK, RATE, Levels, PcmStem, ProofStream


def fixture(path, frames, frequencies):
    with wave.open(str(path), "wb") as target:
        target.setparams((2, 2, RATE, frames, "NONE", "not compressed"))
        for start in range(0, frames, BLOCK):
            data = array("h", (round(12000 * math.sin(2 * math.pi * freq * n / RATE))
                               for n in range(start, min(frames, start + BLOCK))
                               for freq in frequencies))
            if sys.byteorder != "little":
                data.byteswap()
            target.writeframesraw(data.tobytes())


def fixture_plan():
    # 0.5 s normal + 0.5 source seconds slowed/muted to 1 s + 0.5 s normal.
    return AudioPlan(Timeline((Span("a", 4800, 28800),
                               Span("b", 28800, 52800, Fraction(1, 2), False),
                               Span("c", 52800, 76800))),
                     12000, fade_in=4800, fade_out=9600)


def capture(plan, source_path, music_path, *, start=0, levels=Levels()):
    source, music = PcmStem(source_path), PcmStem(music_path)
    stream = ProofStream(plan, source, music, start=start, levels=levels)
    mix, monitored = array("f"), array("f")
    begin, cpu = time.perf_counter(), time.process_time()
    try:
        stream.start()
        while (block := stream.pull()) is not None:
            mix.extend(block.mix)
            monitored.extend(block.monitored)
        result = {"wall_s": time.perf_counter() - begin,
                  "process_cpu_s": time.process_time() - cpu,
                  "worker_cpu_s": stream.worker_cpu_s,
                  "queue_high_water_blocks": stream.high_water,
                  "source_cache_blocks": source.high_water,
                  "music_cache_blocks": music.high_water}
    finally:
        stream.stop()
        source.close()
        music.close()
    return mix, monitored, result


def reference(ffmpeg, source, music, output):
    # Independently written for the fixture's stated shape, NOT compiled from
    # AudioPlan: same-bug agreement between two users of the planner proves little.
    graph = (
        "[0:a]asplit=2[s0][s1];"
        "[s0]atrim=start_sample=4800:end_sample=28800,asetpts=PTS-STARTPTS[a];"
        "anullsrc=r=48000:cl=stereo,atrim=end_sample=48000[z];"
        "[s1]atrim=start_sample=52800:end_sample=76800,asetpts=PTS-STARTPTS[c];"
        "[a][z][c]concat=n=3:v=0:a=1,volume=0.2[d];"
        "[1:a]atrim=end_sample=96000,asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp,"
        "afade=t=in:ss=0:ns=4800,afade=t=out:ss=86400:ns=9600,volume=0.8[m];"
        "[d][m]amix=inputs=2:normalize=0:duration=first:dropout_transition=0[out]"
    )
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-v", "error", "-y",
               "-i", str(source), "-stream_loop", "-1", "-i", str(music),
               "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_f32le",
               "-f", "f32le", str(output)]
    subprocess.run(command, check=True, capture_output=True, timeout=30)
    values = array("f")
    values.frombytes(output.read_bytes())
    if sys.byteorder != "little":
        values.byteswap()
    return values, command


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def controls(plan, source_path, music_path):
    source, music = PcmStem(source_path), PcmStem(music_path)
    stream = ProofStream(plan, source, music)
    latencies, block_cost, lateness = [], [], []
    begin, cpu = time.perf_counter(), time.process_time()
    try:
        stream.start()
        due = time.perf_counter()
        for i in range(80):
            time.sleep(max(0, due - time.perf_counter()))
            lateness.append(max(0, 1000 * (time.perf_counter() - due)))
            before = time.perf_counter()
            revision = stream.set_levels(music=0.5 if i % 2 else 1.0,
                                         dvr=0.25, muted=(i % 3 == 0))
            block = stream.pull()
            assert block.revision == revision
            if i % 3 == 0:
                assert not any(block.monitored)
            latencies.append(block.control_latency_ms)
            block_cost.append(1000 * (time.perf_counter() - before))
            due += BLOCK / RATE
        begin_prime = time.perf_counter()
        generation = stream.reprime(plan, 87321)
        block = stream.pull()
        assert (block.generation, block.start) == (generation, 87321)
        prime_ms = 1000 * (time.perf_counter() - begin_prime)
        cpu_s, wall_s = time.process_time() - cpu, time.perf_counter() - begin
        begin_stop = time.perf_counter()
        stream.stop()
        return {"label": "fake sink; scheduler and CPU timings, NOT audible latency",
                "updates": len(latencies), "control_ms_p95": percentile(latencies, .95),
                "control_ms_max": max(latencies), "pull_ms_p95": percentile(block_cost, .95),
                "scheduler_lateness_ms_max": max(lateness), "reprime_ms": prime_ms,
                "stop_ms": 1000 * (time.perf_counter() - begin_stop),
                "cpu_s": cpu_s, "wall_s": wall_s, "cpu_per_wall": cpu_s / wall_s,
                "queue_high_water_blocks": stream.high_water,
                "discarded_stale_blocks": stream.discarded,
                "worker_alive_after_stop": stream.thread.is_alive()}
    finally:
        stream.stop()
        source.close()
        music.close()


def dependency_inventory():
    # Import feasibility only. Never enumerate/open streams or access microphones.
    import sounddevice
    versions = {}
    for name in ("sounddevice", "cffi", "pycparser"):
        dist = metadata.distribution(name)
        licenses = {}
        for item in dist.files or ():
            if "license" in str(item).lower() or "copying" in str(item).lower():
                path = dist.locate_file(item)
                if path.is_file():
                    licenses[str(item)] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                           "text": path.read_text(errors="replace")}
        versions[name] = {"version": dist.version,
                          "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
                          "project_urls": dist.metadata.get_all("Project-URL"),
                          "license_files": licenses}
    library = Path(sounddevice._libname)
    notice = library.parent / "README.md"
    return {"packages": versions, "portaudio": sounddevice.get_portaudio_version(),
            "loaded_library": sounddevice._libname,
            "loaded_library_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
            "portaudio_supplied_notice": notice.read_text(encoding="utf-8"),
            "audio_stream_opened": False, "device_enumeration_requested": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    source, music = root / "synthetic_source.wav", root / "synthetic_music.wav"
    fixture(source, 96000, (440, 660))
    fixture(music, 12000, (880, 1100))
    plan = fixture_plan()
    values, muted, speed = capture(plan, source, music)
    assert len(values) == 192000 and not any(muted)
    expected, command = reference(args.ffmpeg, source, music, root / "reference.f32")
    assert len(values) == len(expected), (len(values), len(expected))
    error = max(abs(a - b) for a, b in zip(values, expected))
    assert error <= 1e-5, error
    seeks = (1, 4799, 4800, 11999, 12000, 23999, 24000, 48001, 71999, 72000, 86400, 87321, 95999)
    seek_error = 0.0
    for position in seeks:
        part, silence, _ = capture(plan, source, music, start=position)
        assert not any(silence)
        seek_error = max(seek_error, max(abs(a-b) for a, b in
                         zip(part, expected[position * 2:])))
        assert len(part) == len(expected) - position * 2
    assert seek_error <= 1e-5, seek_error
    data = array("f", values)
    if sys.byteorder != "little":
        data.byteswap()
    (root / "mix.f32").write_bytes(data.tobytes())
    result = {"scope": "synthetic PCM/math/thread proof only; no physical audio or video",
              "rate": RATE, "channels": 2, "output_frames": len(values) // 2,
              "max_reference_error": error, "nonzero_seek_count": len(seeks),
              "max_seek_reference_error": seek_error, "capture": speed,
              "controls": controls(plan, source, music),
              "reference_command": command,
              "media_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in (source, music)},
              "ffmpeg_sha256": hashlib.sha256(args.ffmpeg.read_bytes()).hexdigest()}
    if args.inventory:
        inventory = dependency_inventory()
        (root / "dependency_inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
        result["dependency_versions"] = {k: v["version"] for k, v in inventory["packages"].items()}
        result["portaudio"] = inventory["portaudio"]
    (root / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
