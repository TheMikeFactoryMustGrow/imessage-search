"""Synthetic attributedBody fixtures (NSArchiver streamtyped of fake text).

No private message content. Same corpus as the hermetic unit tests.
"""
import base64

_B64 = {
    "SHORT": "BAtzdHJlYW10eXBlZIHoA4QBQISEhBJOU0F0dHJpYnV0ZWRTdHJpbmcAhIQITlNPYmplY3QAhZKEhIQITlNTdHJpbmcBlIQBKxNIZWxsbyBmcm9tIHRoZSBibG9ihoQCaUkBE5KEhIQMTlNEaWN0aW9uYXJ5AJSEAWkAhoY=",
    "LONG": "BAtzdHJlYW10eXBlZIHoA4QBQISEhBJOU0F0dHJpYnV0ZWRTdHJpbmcAhIQITlNPYmplY3QAhZKEhIQITlNTdHJpbmcBlIQBK4HIAHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4hoQCaUkBgcgAkoSEhAxOU0RpY3Rpb25hcnkAlIQBaQCGhg==",
    "UNICODE": "BAtzdHJlYW10eXBlZIHoA4QBQISEhBJOU0F0dHJpYnV0ZWRTdHJpbmcAhIQITlNPYmplY3QAhZKEhIQITlNTdHJpbmcBlIQBKw5jYWbDqSDimJUgdGVzdIaEAmlJAQuShISEDE5TRGljdGlvbmFyeQCUhAFpAIaG",
    "ATTACH": "BAtzdHJlYW10eXBlZIHoA4QBQISEhBJOU0F0dHJpYnV0ZWRTdHJpbmcAhIQITlNPYmplY3QAhZKEhIQITlNTdHJpbmcBlIQBKwPvv7yGhAJpSQEBkoSEhAxOU0RpY3Rpb25hcnkAlIQBaQCGhg==",
}

DECODED = {
    "SHORT": "Hello from the blob",
    "LONG": "x" * 200,
    "UNICODE": "café ☕ test",
    "ATTACH": "[attachment]",
}

OBJ_REPLACEMENT = "￼"


def blob(key: str) -> bytes:
    return base64.b64decode(_B64[key])
