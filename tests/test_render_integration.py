"""Optional integration: requires local FFmpeg with libx264, and is skipped otherwise."""
import shutil
import subprocess
import pytest
from clipforge.project import Project,Clip,TextOverlay
from clipforge.render import render,inspect


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),reason="FFmpeg not on PATH")
def test_short_real_export(tmp_path):
    source=tmp_path/"source.mp4"
    proc=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                         "-f","lavfi","-i","testsrc2=size=320x180:rate=30",
                         "-f","lavfi","-i","sine=frequency=440:sample_rate=48000",
                         "-t","7","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(source)],
                         capture_output=True,text=True)
    if proc.returncode: pytest.skip("ffmpeg H.264 unavailable: "+proc.stderr[:300])
    p=Project(video_path=str(source),duration=7,aspect="1:1",framing="pad",captions=False)
    p.clips=[Clip(.3,2.6,"First"),Clip(3,5.8,"Second")]
    p.transition="fade";p.transition_seconds=.3
    p.overlays=[TextOverlay("Original title",0.3,1.2)]
    target=tmp_path/"out.mp4"
    render(p,str(target))
    assert target.exists() and target.stat().st_size>1000
    assert 4.5<float(inspect(str(target))["duration"])<5.4
    assert target.with_suffix(".srt").exists()

@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),reason="FFmpeg not on PATH")
def test_caption_voice_music_blur_pipeline(tmp_path):
    from clipforge.project import Voice,Segment,Word
    source=tmp_path/"source.mp4"
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                    "-f","lavfi","-i","testsrc2=size=320x180:rate=30",
                    "-f","lavfi","-i","sine=frequency=400:sample_rate=48000",
                    "-t","3","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(source)],
                   check=True)
    audio=tmp_path/"voice.wav"
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                    "-f","lavfi","-i","sine=frequency=950:sample_rate=48000","-t","0.6",str(audio)],check=True)
    p=Project(video_path=str(source),duration=3,aspect="9:16",framing="blur",captions=True)
    p.clips=[Clip(.2,1.8,"One")]
    p.segments=[Segment(.2,1.3,"Testing captions",[Word(.2,.55,"Testing"),Word(.7,1.3,"captions")])]
    p.voices=[Voice(str(audio),.25)]
    p.music_path=str(audio)
    p.overlays=[TextOverlay("Amazing",0,.5)]
    out=tmp_path/"with-voice.mp4"
    render(p,str(out))
    assert out.stat().st_size>1000
    assert "Testing" in out.with_suffix(".srt").read_text(encoding="utf-8-sig")

@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg missing")
def test_batch_export_with_silent_source(tmp_path):
    from clipforge.render import render_batch
    source=tmp_path/"silent.mp4"
    subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                    "-f","lavfi","-i","testsrc2=size=320x180:rate=30",
                    "-t","3","-c:v","libx264","-pix_fmt","yuv420p",str(source)],check=True)
    p=Project(video_path=str(source),duration=3,aspect="1:1",framing="crop",captions=False)
    p.clips=[Clip(.1,1.3,"Greeting",hook="Hey everybody"),Clip(1.5,2.6,"Information")]
    files=render_batch(p,str(tmp_path/"batch"))
    assert len(files)==2
    assert all(f.stat().st_size>500 for f in files)
    assert (tmp_path/"batch"/"clip-manifest.csv").is_file()
