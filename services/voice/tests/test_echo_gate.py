import array
import math

from nova_sonic.audio import EchoGate, rms_int16


def tone(amplitude: int, n: int = 512) -> bytes:
    return array.array("h", [int(amplitude * math.sin(i / 7.0)) for i in range(n)]).tobytes()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make_gate(**kw):
    g = EchoGate(min_rms=250, ratio=2.5, hold_chunks=3, k_init=0.25, **kw)
    g.clock = Clock()
    return g


def test_rms_scales_with_amplitude():
    assert rms_int16(b"") == 0.0
    assert 150 < rms_int16(tone(300)) < 300 and rms_int16(tone(9000)) > 5000


def test_everything_passes_when_assistant_is_silent():
    g = make_gate()
    assert g.should_forward(tone(10), playing=False)


def test_echo_of_loud_syllable_is_dropped_even_at_onset():
    g = make_gate()
    g.note_output(tone(8000))              # assistant says "HELLO" loudly: output RMS ~5657
    echo = tone(1700)                      # what the mic hears back, RMS ~1200
    # expected echo = k*out = 0.25*5657 = 1414; threshold = 2.5*1414 = 3536 > 1200 -> dropped, no calibration needed
    assert not g.should_forward(echo, playing=True)


def test_speech_during_pause_passes_at_min_rms():
    g = make_gate()
    g.note_output(tone(8000))
    g.clock.t += 1.0                       # output was >0.3 s ago: assistant is between words
    assert g.recent_output_rms() == 0.0
    assert g.should_forward(tone(400), playing=True)   # RMS ~283 > min 250


def test_coupling_learns_and_real_speech_still_passes_over_loud_output():
    g = make_gate()
    out, echo, speech = tone(8000), tone(700), tone(9000)   # echo RMS ~495, speech ~6364
    for _ in range(400):
        g.note_output(out)
        assert not g.should_forward(echo, playing=True)
    assert 0.08 <= g.k < 0.25              # decayed from 0.25 toward the observed 495/5657 = 0.0875, never below K_MIN
    g.note_output(out)
    assert g.should_forward(speech, playing=True)       # 6364 > 2.5*k*5657 for any k < 0.45
    # hold keeps the next chunks open, then closes
    assert all(g.should_forward(echo, playing=True) for _ in range(3))
    assert not g.should_forward(echo, playing=True)


def test_half_duplex_and_disabled():
    assert not EchoGate(half_duplex=True).should_forward(tone(20000), playing=True)
    assert EchoGate(enabled=False, half_duplex=False).should_forward(tone(5), playing=True)


def test_quiet_echo_between_words_does_not_drag_coupling_down():
    """Regression: averaging pulled k to the floor and real echo got through."""
    g = make_gate()
    loud_out, loud_echo = tone(8000), tone(1700)        # k_true = 1200/5657 = 0.21
    g.note_output(loud_out)
    assert not g.should_forward(loud_echo, playing=True)
    # 200 near-silent gaps while recent output is still loud (max over window)
    for _ in range(200):
        g.note_output(loud_out)
        assert not g.should_forward(tone(60), playing=True)
    # The genuine echo of the next loud syllable must still be dropped.
    g.note_output(loud_out)
    assert not g.should_forward(loud_echo, playing=True)
    assert g.k >= 0.19
