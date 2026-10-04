import array
import math

from nova_sonic.audio import rms_int16, should_forward


def tone(amplitude: int, n: int = 512) -> bytes:
    return array.array("h", [int(amplitude * math.sin(i / 7.0)) for i in range(n)]).tobytes()


def test_rms_scales_with_amplitude():
    assert rms_int16(b"") == 0.0
    quiet, loud = rms_int16(tone(300)), rms_int16(tone(9000))
    assert 150 < quiet < 300 and loud > 5000


def test_everything_passes_when_assistant_is_silent():
    assert should_forward(tone(10), playing=False, gate_rms=1500)
    assert should_forward(tone(10), playing=False, gate_rms=1500, half_duplex=True)


def test_gate_drops_echo_but_keeps_real_speech_during_playback():
    echo, speech = tone(400), tone(6000)
    assert not should_forward(echo, playing=True, gate_rms=1500)
    assert should_forward(speech, playing=True, gate_rms=1500)
    assert should_forward(echo, playing=True, gate_rms=0)  # gate disabled


def test_half_duplex_mutes_everything_during_playback():
    assert not should_forward(tone(20000), playing=True, gate_rms=1500, half_duplex=True)
