from clipforge.project import Project,Clip,Segment,Word,stamp
from clipforge.transcription import make_srt,caption_cues
from clipforge.render import export_duration,overlap_duration,frame_filter,PRESETS
from clipforge.agent import parse_json,_extract_candidates,windows


def sample():
    p=Project(duration=100)
    p.segments=[Segment(10,22,"Hello world this is interesting",[Word(10,10.5,"Hello"),Word(10.5,11,"world")])]
    p.clips=[Clip(10,20,"A"),Clip(30,40,"B")]
    return p


def test_project_roundtrip(tmp_path):
    p=sample()
    path=tmp_path/"something.clipforge.json"
    p.save(str(path))
    q=Project.load(str(path))
    assert q.clips[0].title=="A"
    assert q.segments[0].words[0].text=="Hello"
    assert q.project_file==str(path)


def test_timecode_and_cues():
    p=sample()
    assert stamp(3661.23)=="01:01:01.230"
    cues=caption_cues(p)
    assert cues[0][0]==0.
    assert "Hello" in cues[0][2]
    assert "00:00:00,000" in make_srt(cues)


def test_export_length():
    p=sample()
    assert export_duration(p)==20
    p.transition="fade";p.transition_seconds=.4
    assert overlap_duration(p)==.4
    assert abs(export_duration(p)-19.6)<.001


def test_filters():
    for key in ["blur","pad","crop"]:
        s,is_complex=frame_filter(1080,1920,key)
        assert ("[vout]" in s)==is_complex
    assert "4:5" in PRESETS


def test_model_json_parser():
    assert parse_json('```json\n[{"start":10,"end":18}]\n```')[0]["start"]==10
    p=sample()
    text="[10.0-22.0] Hello world this is interesting\n"
    clips=_extract_candidates([{"start":10,"end":18,"title":"A","score":88}],p,text)
    assert len(clips)==1 and clips[0].score==88
    assert _extract_candidates([{"start":11,"end":12}],p,text)==[]


def test_windows_cover_all():
    p=sample()
    p.segments=[Segment(i,i+1,"Words "*90) for i in range(30)]
    chunks=windows(p,max_chars=1000)
    assert len(chunks)>=15
    assert "[29.0-30.0]" in chunks[-1]
