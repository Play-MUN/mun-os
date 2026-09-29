"""Identifiable card errors: a stable code for software, a sentence for people."""


class CardError(Exception):
    """A card was rejected. `code` is stable and documented; `detail` is optional context."""

    def __init__(self, code: str, message: str, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


# Every code the validator can emit, with the user-facing meaning.
# docs/game-cards.md refers readers to this list.
ERROR_CODES = {
    "manifest_missing": "No se encontró mun.toml ni neptune.toml en la tarjeta",
    "manifest_ambiguous": "La tarjeta tiene mun.toml y neptune.toml a la vez",
    "manifest_too_large": "El manifiesto supera el tamaño permitido",
    "manifest_syntax": "El manifiesto no es TOML válido",
    "manifest_field": "Falta un campo obligatorio o su tipo es incorrecto",
    "schema_unsupported": "La versión de esquema del manifiesto no es compatible",
    "id_invalid": "El identificador de la tarjeta no es válido",
    "kind_unsupported": "El tipo de contenido no es compatible",
    "arch_unsupported": "La arquitectura del contenido no es compatible",
    "profile_unsupported": "El perfil de ejecución no es compatible",
    "version_invalid": "La versión del contenido no tiene un formato válido",
    "path_unsafe": "Una ruta del manifiesto sale de la tarjeta o no es relativa",
    "path_missing": "Un archivo o carpeta referenciado no existe",
    "path_symlink": "Una ruta referenciada es un enlace simbólico",
    "path_type": "Una ruta referenciada no es del tipo esperado",
    "cover_invalid": "La portada no es un PNG válido",
    "cover_too_large": "La portada supera el tamaño o las dimensiones permitidas",
    "image_not_ext4": "La imagen no contiene un sistema de archivos ext4",
    "image_needs_recovery": "El sistema de archivos necesita recuperación y no se monta",
    "image_has_errors": "El sistema de archivos está marcado con errores",
    "image_partitioned": "La imagen tiene tabla de particiones; v0 usa ext4 directo",
    "source_unreadable": "No se pudo leer la tarjeta",
}
