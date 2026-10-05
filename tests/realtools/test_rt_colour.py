"""
OpenColorIO's built-in config and EXR headers, with the real libraries.

EXRs are written by OpenImageIO with the header a renderer or an ACES
container would carry, then shown by the player's image engine offscreen.
"""


import numpy as np
import pytest

pytestmark = pytest.mark.realtools
oiio = pytest.importorskip("OpenImageIO")
pytest.importorskip("PyOpenColorIO")

from slate.core.domain.color_manager import ColorManager  # noqa: E402

AP1 = (0.713, 0.293, 0.165, 0.830, 0.128, 0.044, 0.32168, 0.33767)
AP0 = (0.7347, 0.2653, 0.0, 1.0, 0.0001, -0.077, 0.32168, 0.33767)


def test_builtin_config_loads_and_offers_what_the_player_lists():
    cm = ColorManager.instance()
    assert cm.is_available()
    names = set(cm.get_colorspaces())
    common = [name for name, _label in cm.get_common_colorspaces()]
    assert "sRGB - Texture" in common, "sRGB was never offered: 'sRGB Encoding' is not in the config"
    assert set(common) <= names
    from slate.gui.widgets.media_engines.image_engine import _NAMED_SPACES
    assert {v for v in _NAMED_SPACES.values() if v} <= names


def _exr(path, attrs):
    spec = oiio.ImageSpec(64, 48, 3, oiio.HALF)
    for key, value in attrs.items():
        if key == "chromaticities":
            spec.attribute(key, oiio.TypeDesc("float[8]"), value)
        else:
            spec.attribute(key, value)
    red = np.zeros((48, 64, 3), np.float32)
    red[..., 0] = 0.5
    out = oiio.ImageOutput.create(str(path))
    assert out.open(str(path), spec)
    out.write_image(red)
    out.close()
    return path


def _shown(qtbot, path):
    from slate.gui.widgets.media_engines.image_engine import ImageEngine
    engine = ImageEngine()
    engine.load(str(path))
    qtbot.waitUntil(lambda: engine.current_image is not None, timeout=5000)
    image = engine.current_image
    return engine.input_space, (image.width(), image.height()), image.pixelColor(10, 10).getRgb()


@pytest.mark.parametrize("attrs,space", [
    ({"colorspace": "ACEScg"}, "ACEScg"),
    ({"chromaticities": AP1}, "ACEScg"),
    ({"acesImageContainerFlag": 1, "chromaticities": AP0}, "ACES2065-1"),
    ({"colorspace": "srgb"}, "sRGB - Texture"),
    ({}, "Linear Rec.709 (sRGB)"),  # nothing said: OpenImageIO's own reading
])
def test_exr_is_shown_in_the_colourspace_its_header_names(qtbot, tmp_path, attrs, space):
    """
    FOUND + FIXED: OpenImageIO 3 marks every EXR "lin_rec709", and that was
    read first - ACEScg renders and ACES containers all showed as Rec.709.
    """
    shown_space, size, _rgb = _shown(qtbot, _exr(tmp_path / "f.exr", attrs))
    assert shown_space == space and size == (64, 48)


def test_a_colourspace_the_config_does_not_know_is_not_shown_as_noise(qtbot, tmp_path):
    unknown = _shown(qtbot, _exr(tmp_path / "u.exr", {"colorspace": "Output - Rec.709 (made up)"}))
    plain = _shown(qtbot, _exr(tmp_path / "p.exr", {"colorspace": "ACEScg"}))
    assert unknown[0] == ColorManager.instance().scene_linear_space()
    assert unknown[2] == plain[2]
