"""Pruebas unitarias de app.tax_id (Requirements 3.3, 5.6)."""

import pytest

from app.tax_id import is_valid, kind, normalize


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12345678Z", "12345678Z"),
            ("12345678z", "12345678Z"),
            ("12 345 678-z", "12345678Z"),
            ("x-1234567-l", "X1234567L"),
            (" b 1234567 4 ", "B12345674"),
            ("A-58\t818\n501", "A58818501"),
            ("--  --", ""),
            ("", ""),
        ],
    )
    def test_removes_separators_and_uppercases(self, raw, expected):
        assert normalize(raw) == expected


class TestKind:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("12345678Z", "NIF"),
            ("12345678A", "NIF"),  # formato NIF aunque la letra sea incorrecta
            ("X1234567L", "NIE"),
            ("Y0000000Z", "NIE"),
            ("Z0000000M", "NIE"),
            ("A58818501", "CIF"),
            ("P1234567D", "CIF"),
            ("G1234567J", "CIF"),
        ],
    )
    def test_recognized_formats(self, value, expected):
        assert kind(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "1234567Z",  # 7 dígitos
            "123456789Z",  # 9 dígitos
            "123456789",  # NIF sin letra
            "W1234567",  # CIF corto
            "I1234567A",  # I no es letra inicial de CIF
            "O1234567A",  # O tampoco
            "A1234567K",  # control CIF fuera de 0-9/A-J
            "X12345678",  # NIE con dígito final
            "12345678z",  # sin normalizar
            "12345678 Z",  # sin normalizar
            "12345678Z\n",  # salto de línea final
            "١٢٣٤٥٦٧٨Z",  # dígitos no ASCII
        ],
    )
    def test_unrecognized_formats_return_none(self, value):
        assert kind(value) is None


class TestIsValidNif:
    @pytest.mark.parametrize("value", ["12345678Z", "00000000T", "99999999R"])
    def test_valid(self, value):
        assert is_valid(value)

    @pytest.mark.parametrize("value", ["12345678A", "00000000R"])
    def test_wrong_letter(self, value):
        assert not is_valid(value)


class TestIsValidNie:
    @pytest.mark.parametrize("value", ["X1234567L", "Y0000000Z", "Z0000000M", "X0000000T"])
    def test_valid(self, value):
        assert is_valid(value)

    @pytest.mark.parametrize("value", ["X1234567A", "Y0000000T", "Z0000000Z"])
    def test_wrong_letter(self, value):
        assert not is_valid(value)


class TestIsValidCif:
    @pytest.mark.parametrize(
        "value",
        [
            # Letras ABEH -> control numérico
            "A58818501",
            "B12345674",
            "A00000000",
            "H12345674",
            # Letras PQRSNW -> control con letra
            "P1234567D",
            "Q0000000J",
            "W1234567D",
            # Resto -> dígito o letra
            "G12345674",
            "G1234567D",
            "J00000000",
            "J0000000J",
        ],
    )
    def test_valid(self, value):
        assert is_valid(value)

    @pytest.mark.parametrize(
        "value",
        [
            # Control numérico requerido: letra equivalente no vale, dígito erróneo tampoco
            "A5881850A",
            "B1234567D",
            "B12345675",
            # Control con letra requerido: dígito equivalente no vale, letra errónea tampoco
            "P12345674",
            "Q00000000",
            "P1234567E",
            # Cualquiera de los dos: ambos erróneos
            "G12345675",
            "G1234567E",
        ],
    )
    def test_wrong_control(self, value):
        assert not is_valid(value)


class TestIsValidOther:
    @pytest.mark.parametrize("value", ["", "ABC", "1234567Z", "I1234567A", "12345678z"])
    def test_unrecognized_format_is_invalid(self, value):
        assert not is_valid(value)

    def test_validation_after_normalize(self):
        assert is_valid(normalize("12.345.678-z".replace(".", " ")))
        assert is_valid(normalize("x 1234567 l"))
        assert is_valid(normalize("b-1234567-4"))
