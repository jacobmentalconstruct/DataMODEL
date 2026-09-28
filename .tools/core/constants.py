PRODUCT_VERSION = "0.1.0"
INSTANCE_SCHEMA_VERSION = 1
DATABASE_SCHEMA_VERSION = 8
TOOL_CONTRACT_VERSION = 1
CONTROL_PLANE_VERSION = 1

# Interpreter compatibility, recorded by measurement rather than asserted.
#
# VERIFIED_PYTHON lists the (major, minor) versions on which the product test suite has
# actually been run and passed. KNOWN_INCOMPATIBLE lists versions measured to break.
# An interpreter outside both sets is neither endorsed nor refused: it runs, and the
# product says that it is unverified. Unobserved means unknown, never absent.
VERIFIED_PYTHON = frozenset({(3, 10), (3, 11), (3, 12), (3, 13)})
KNOWN_INCOMPATIBLE: frozenset = frozenset()


def python_support(version: tuple = ()) -> tuple:
    """Classify an interpreter as verified, unverified, or incompatible.

    Returns (support, detail). `detail` is None when verified, and otherwise a sentence
    stating what is not known, suitable for a limitations list or a refusal message.
    """
    import sys

    running = tuple(version or sys.version_info[:2])
    label = "%d.%d" % running
    verified = ", ".join("%d.%d" % v for v in sorted(VERIFIED_PYTHON))
    if running in KNOWN_INCOMPATIBLE:
        return (
            "incompatible",
            "CPython " + label + " is known to be incompatible with this product; "
            "verified interpreters are " + verified,
        )
    if running in VERIFIED_PYTHON:
        return "verified", None
    return (
        "unverified",
        "running on CPython " + label + ", which is outside the verified set (" + verified
        + "); behaviour here has not been measured",
    )
