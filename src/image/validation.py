"""Primeira etapa da pipeline: valida e normaliza bytes de imagem não confiáveis."""

import warnings
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 8_192
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


class ImageValidationError(ValueError):
    """Erro seguro para exibir ao chamador, sem incluir dados da mídia."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    content: bytes
    mime_type: str
    width: int
    height: int
    source_sha256: str


def validate_image(content: bytes) -> ValidatedImage:
    """Retorna uma imagem decodificada e regravada sem metadados.

    Os limites são aplicados antes da decodificação completa. Este bloco não
    classifica conteúdo nem autoriza publicação.
    """
    if not content:
        raise ImageValidationError("INVALID_IMAGE", "A imagem está vazia.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ImageValidationError(
            "IMAGE_TOO_LARGE", "A imagem excede o limite de tamanho."
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(
                BytesIO(content), formats=sorted(ALLOWED_FORMATS)
            ) as source:
                if source.format not in ALLOWED_FORMATS:
                    raise ImageValidationError(
                        "INVALID_IMAGE", "Formato de imagem não permitido."
                    )
                if getattr(source, "n_frames", 1) != 1:
                    raise ImageValidationError(
                        "INVALID_IMAGE", "Imagens animadas não são permitidas."
                    )

                width, height = source.size
                if (
                    width < 1
                    or height < 1
                    or width > MAX_IMAGE_SIDE
                    or height > MAX_IMAGE_SIDE
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise ImageValidationError(
                        "IMAGE_DIMENSIONS_EXCEEDED",
                        "As dimensões da imagem excedem o limite.",
                    )

                source.load()
                oriented = ImageOps.exif_transpose(source)
                has_alpha = (
                    "A" in oriented.getbands() or "transparency" in oriented.info
                )
                mode = "RGBA" if has_alpha else "RGB"
                pixels = oriented.convert(mode)

                # Copiar só pixels para uma imagem nova impede herdar EXIF e outros metadados.
                clean = Image.new(mode, pixels.size)
                clean.paste(pixels)
                output = BytesIO()
                output_format = "PNG" if has_alpha else "JPEG"
                clean.save(output, format=output_format)
                normalized = output.getvalue()
                if len(normalized) > MAX_UPLOAD_BYTES:
                    raise ImageValidationError(
                        "IMAGE_TOO_LARGE",
                        "A imagem normalizada excede o limite de tamanho.",
                    )

                return ValidatedImage(
                    content=normalized,
                    mime_type="image/png" if has_alpha else "image/jpeg",
                    width=clean.width,
                    height=clean.height,
                    source_sha256=sha256(content).hexdigest(),
                )
    except ImageValidationError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise ImageValidationError(
            "INVALID_IMAGE", "Arquivo de imagem inválido ou corrompido."
        ) from exc
