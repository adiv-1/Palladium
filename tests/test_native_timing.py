import av
import numpy as np
import pytest
from apps.api.video import Frame,encode,write_clip,align_frame_timing

def test_enhancement_writer_cannot_slow_native60fps_motion():
    source=[Frame(i/60,i,b'original') for i in range(120)]
    output=[Frame(i/30,i,b'enhanced') for i in range(120)]
    aligned=align_frame_timing(source,output)
    assert [f.timestamp for f in aligned]==[f.timestamp for f in source]
    assert all(f.jpeg==b'enhanced' for f in aligned)
    assert aligned[-1].timestamp<2
    with pytest.raises(ValueError):align_frame_timing(source,output[:-1])

def test_review_video_encoder_preserves_real60fps_pts_and_rate(tmp_path):
    image=encode(np.full((64,96,3),120,np.uint8))
    frames=[Frame(i/60,i,image) for i in range(120)]
    target=tmp_path/'review.mp4';write_clip(frames,target,'REVIEW COPY')
    with av.open(str(target)) as c:
        rate=float(c.streams.video[0].average_rate)
        stamps=[float(f.time) for f in c.decode(video=0)]
    assert len(stamps)==120
    assert rate==pytest.approx(60,rel=.01)
    assert np.diff(stamps)==pytest.approx(np.full(119,1/60),abs=.0001)


def test_uploaded_display_rotation_is_respected_before_analysis():
    from apps.api.video import display_image
    pixels=np.zeros((2,3,3),np.uint8);pixels[0,0]=[10,20,30]
    class PhoneFrame:
        rotation=-90
        def to_ndarray(self,format):return pixels.copy()
    result=display_image(PhoneFrame())
    assert result.shape==(3,2,3)
    assert np.array_equal(result,np.rot90(pixels,k=-1))
