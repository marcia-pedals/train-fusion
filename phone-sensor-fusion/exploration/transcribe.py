"""Whisper transcripts of the recordings, to find station announcements (which fix which
stations a ride called at: journey.toml in the labels).

Writes data/phone-sensor-fusion/whisper/<id>.csv (start_ms, end_ms, text). These stay
out of the repository: the microphone also hears other passengers' conversations. Only
the announcements quoted in journey.toml are kept.

Needs the model file (1.6 GB), once:
    curl -L -o data/phone-sensor-fusion/whisper/ggml-large-v3-turbo.bin \\
        https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin

    python -m exploration.transcribe

Each 30 s window is decoded on its own (`-mc 0`). By default whisper.cpp prompts each
window with the text of the previous one, and on hours of train noise that locks it into
repeating one hallucinated line for the rest of the ride, so that almost every announcement
was lost. Without the context it hears most of them (next stop, train number, doors
closing). Windows with no speech still come out as filler ("Thank you."); grep for
station names and "doors" rather than reading it. Silero VAD (`--vad`) is no help: it
doesn't count the PA over the noise as speech.
"""

import subprocess

from common import paths
from common.recording import load_all


def main():
    model = paths.derived("whisper", "ggml-large-v3-turbo.bin")
    assert model.exists(), f"download the model first (see this file's docstring): {model}"
    for rec in load_all():
        if paths.derived("whisper", f"{rec.id}.csv").exists():
            continue  # delete the csv to redo one
        wav = paths.derived("whisper", f"{rec.id}.16k.wav")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(rec.dir / "audio_000.m4a"),
                        "-ar", "16000", "-ac", "1", str(wav)], check=True)
        subprocess.run(["whisper-cli", "-m", str(model), "-f", str(wav), "-l", "en", "-mc", "0",
                        "-ocsv", "-of", str(paths.derived("whisper", rec.id)), "-np"], check=True,
                       stdout=subprocess.DEVNULL)
        wav.unlink()
        print("wrote", paths.derived("whisper", f"{rec.id}.csv"))


if __name__ == "__main__":
    main()
