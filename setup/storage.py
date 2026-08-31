from whitenoise.storage import CompressedManifestStaticFilesStorage


class NonStrictCompressedManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """
    CompressedManifestStaticFilesStorage that is tolerant to missing
    manifest entries at runtime.

    Django's Manifest staticfiles storage raises ValueError when a template
    references a static file not present in the generated manifest. This
    can cause 500s if collectstatic fails or the manifest is out-of-sync.

    Setting `manifest_strict = False` makes the storage fallback to the
    unhashed name instead of raising, which is safer in production and
    avoids hard crashes while still serving files.
    """

    manifest_strict = False
