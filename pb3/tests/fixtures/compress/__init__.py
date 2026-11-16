"""Fixture IPA giả cho test nén (xem builder.py)."""
from .builder import (COMPRESSIBLE_TEXT, DATE_TIME, compressible_ipa_bytes,
                      corrupt_crc_ipa_bytes, duplicate_ipa_bytes,
                      empty_ipa_bytes, encrypted_ipa_bytes, make_ipa_bytes,
                      random_ipa_bytes, sample_ipa_bytes, unsafe_name_ipa_bytes)

__all__ = ["COMPRESSIBLE_TEXT", "DATE_TIME", "compressible_ipa_bytes",
           "corrupt_crc_ipa_bytes", "duplicate_ipa_bytes", "empty_ipa_bytes",
           "encrypted_ipa_bytes", "make_ipa_bytes", "random_ipa_bytes",
           "sample_ipa_bytes", "unsafe_name_ipa_bytes"]
