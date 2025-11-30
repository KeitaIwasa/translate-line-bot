TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lang": {
                        "type": "string",
                        "description": "ISO 639-1 language code for the translation",
                    },
                    "text": {
                        "type": "string",
                        "description": "Translated text. Preserve mentions such as @John.",
                    },
                },
                "required": ["lang", "text"],
            },
        }
    },
    "required": ["translations"],
}
