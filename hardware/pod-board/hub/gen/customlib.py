"""The two symbols KiCad's library lacks, written as a project symbol library."""
from __future__ import annotations


def _pin(etype, x, y, ang, name, num, length=2.54):
    return (f'(pin {etype} line (at {x} {y} {ang}) (length {length}) '
            f'(name "{name}" (effects (font (size 1.27 1.27)))) '
            f'(number "{num}" (effects (font (size 1.27 1.27)))))')


def _symbol(name, ref, value, footprint, datasheet, descr, body, pins, h):
    props = (
        f'(property "Reference" "{ref}" (at 0 {h + 1.27} 0) (effects (font (size 1.27 1.27))))\n'
        f'(property "Value" "{value}" (at 0 {-h - 1.27} 0) (effects (font (size 1.27 1.27))))\n'
        f'(property "Footprint" "{footprint}" (at 0 {-h - 3.81} 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
        f'(property "Datasheet" "{datasheet}" (at 0 {-h - 6.35} 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
        f'(property "Description" "{descr}" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
    )
    return (f'(symbol "{name}" (pin_names (offset 1.016)) (exclude_from_sim no) (in_bom yes) (on_board yes)\n{props}'
            f'(symbol "{name}_0_1" {body})\n'
            f'(symbol "{name}_1_1" {" ".join(pins)})\n)')


def avc4t774():
    # SN74AVC4T774 (TI SCES620): PW pinout. A side referenced to VCCA, B side to VCCB.
    h = 13.97
    body = f'(rectangle (start -7.62 {h}) (end 7.62 {-h}) (stroke (width 0.254) (type default)) (fill (type background)))'
    L, R = -10.16, 10.16
    pins = [
        _pin("input", L, 10.16, 0, "DIR1", 1), _pin("input", L, 7.62, 0, "DIR2", 2),
        _pin("bidirectional", L, 2.54, 0, "A1", 3), _pin("bidirectional", L, 0, 0, "A2", 4),
        _pin("bidirectional", L, -2.54, 0, "A3", 5), _pin("bidirectional", L, -5.08, 0, "A4", 6),
        _pin("input", L, 5.08, 0, "DIR3", 7), _pin("input", L, -7.62, 0, "DIR4", 8),
        _pin("input", L, -10.16, 0, "~{OE}", 9), _pin("power_in", 0, -h - 2.54, 90, "GND", 10),
        _pin("bidirectional", R, -5.08, 180, "B4", 11), _pin("bidirectional", R, -2.54, 180, "B3", 12),
        _pin("bidirectional", R, 0, 180, "B2", 13), _pin("bidirectional", R, 2.54, 180, "B1", 14),
        _pin("power_in", 2.54, h + 2.54, 270, "VCCB", 15), _pin("power_in", -2.54, h + 2.54, 270, "VCCA", 16),
    ]
    return _symbol("SN74AVC4T774PW", "U", "SN74AVC4T774PW", "Package_SO:TSSOP-16_4.4x5mm_P0.65mm",
                   "https://www.ti.com/lit/ds/symlink/sn74avc4t774.pdf",
                   "4-bit dual-supply transceiver, per-bit direction, 3-state, TSSOP-16", body, pins, h)


def icm42688p():
    # ICM-42688-P (TDK DS-000347), LGA-14 3 x 2.5 mm; pin-out per the EV board note AN-000488.
    h = 10.16
    body = f'(rectangle (start -7.62 {h}) (end 7.62 {-h}) (stroke (width 0.254) (type default)) (fill (type background)))'
    L, R = -10.16, 10.16
    pins = [
        _pin("bidirectional", L, 5.08, 0, "AP_SDO/AP_AD0", 1),
        _pin("passive", R, -2.54, 180, "RESV", 2), _pin("passive", R, -5.08, 180, "RESV", 3),
        _pin("output", R, 5.08, 180, "INT1", 4),
        _pin("power_in", 2.54, h + 2.54, 270, "VDDIO", 5), _pin("power_in", 0, -h - 2.54, 90, "GND", 6),
        _pin("passive", R, -7.62, 180, "RESV", 7), _pin("power_in", -2.54, h + 2.54, 270, "VDD", 8),
        _pin("bidirectional", R, 2.54, 180, "INT2/FSYNC/CLKIN", 9),
        _pin("passive", R, 0, 180, "RESV", 10), _pin("passive", 5.08, -h - 2.54, 90, "RESV", 11),
        _pin("input", L, -5.08, 0, "AP_CS", 12), _pin("input", L, 0, 0, "AP_SCL/AP_SCLK", 13),
        _pin("bidirectional", L, 2.54, 0, "AP_SDA/AP_SDIO/AP_SDI", 14),
    ]
    return _symbol("ICM-42688-P", "U", "ICM-42688-P", "Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y",
                   "https://invensense.tdk.com/products/motion-tracking/6-axis/icm-42688-p/",
                   "6-axis IMU, I2C/SPI, FSYNC input, LGA-14 3 x 2.5 mm", body, pins, h)


def library_text() -> str:
    return ("(kicad_symbol_lib (version 20241209) (generator \"sideline_hub\") (generator_version \"9.0\")\n"
            + avc4t774() + "\n" + icm42688p() + "\n)\n")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "sideline.kicad_sym"
    out.write_text(library_text())
    print("wrote", out)
