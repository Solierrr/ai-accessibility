"""Casos de segurança do primeiro bloco, sem provedor de IA."""

import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from src.image.validation import ImageValidationError, validate_image


def image_bytes(format_name: str = "JPEG", *, with_exif: bool = False) -> bytes:
    image = Image.new("RGB", (3, 2), "red")
    output = BytesIO()
    options = {}
    if with_exif:
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = "metadado privado"
        options["exif"] = exif
    image.save(output, format=format_name, **options)
    return output.getvalue()


class ImageValidationTests(unittest.TestCase):
    def test_reencodes_without_exif_and_applies_orientation(self) -> None:
        result = validate_image(image_bytes(with_exif=True))
        self.assertEqual((result.width, result.height), (2, 3))
        self.assertEqual(result.mime_type, "image/jpeg")
        with Image.open(BytesIO(result.content)) as image:
            self.assertIsNone(image.getexif().get(270))
            self.assertIsNone(image.getexif().get(274))

    def test_rejects_fake_or_truncated_image(self) -> None:
        for content in (b"not a jpeg", image_bytes("PNG")[:20]):
            with self.subTest(content=content[:10]):
                with self.assertRaises(ImageValidationError) as raised:
                    validate_image(content)
                self.assertEqual(raised.exception.code, "INVALID_IMAGE")

    def test_preserves_transparency_in_clean_png(self) -> None:
        image = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
        output = BytesIO()
        image.save(output, format="PNG")

        result = validate_image(output.getvalue())
        self.assertEqual(result.mime_type, "image/png")
        with Image.open(BytesIO(result.content)) as clean:
            self.assertEqual(clean.getpixel((0, 0))[3], 0)

    def test_limits_are_enforced_before_decoding_full_image(self) -> None:
        with patch("src.image.validation.MAX_UPLOAD_BYTES", 10):
            with self.assertRaises(ImageValidationError) as raised:
                validate_image(image_bytes())
            self.assertEqual(raised.exception.code, "IMAGE_TOO_LARGE")

        with patch("src.image.validation.MAX_IMAGE_PIXELS", 5):
            with self.assertRaises(ImageValidationError) as raised:
                validate_image(image_bytes())
            self.assertEqual(raised.exception.code, "IMAGE_DIMENSIONS_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
