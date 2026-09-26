"""Sideline camera hub, rev A: the netlist, as the single source for the
schematic and the board.

The hub sits between Antmicro's Jetson Orin Baseboard and the four camera
heads. The baseboard carries four 2-lane MIPI cameras on two 50-pin FFC
connectors (J7: CSI0 + CSI2, J8: CSI1 + CSI3, with R108 moved to R122 so
CSI3's lanes reach J8). The hub turns each 50-pin into two Raspberry Pi 5
style 22-pin camera ports, and adds what the baseboard lacks: a per-head
power switch, per-head enable GPIOs, a frame-sync bus that makes one head
the master and the rest slaves, an IMU that samples on the frame pulse,
and a humidity sensor for the sealed pod.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HEADS = ("FARL", "NEARL", "FARR", "NEARR")
HEAD_LABEL = {"FARL": "FAR-L", "NEARL": "NEAR-L", "FARR": "FAR-R", "NEARR": "NEAR-R"}


@dataclass
class Part:
    ref: str
    lib_id: str
    footprint: str
    value: str
    nets: dict[str, str]                 # pin number -> net; missing pins are no-connect
    mpn: str = ""
    lcsc: str = ""
    descr: str = ""
    dnp: bool = False
    group: str = ""                      # schematic block


PARTS: list[Part] = []


def add(*a, **k) -> Part:
    p = Part(*a, **k)
    PARTS.append(p)
    return p


R0402 = "Resistor_SMD:R_0402_1005Metric"
C0402 = "Capacitor_SMD:C_0402_1005Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
LED0603 = "LED_SMD:LED_0603_1608Metric"


def R(ref, value, a, b, group, mpn="", lcsc="", dnp=False):
    return add(ref, "Device:R", R0402, value, {"1": a, "2": b}, mpn=mpn, lcsc=lcsc, group=group, dnp=dnp)


def C(ref, value, a, b, group, fp=C0402, mpn="", lcsc=""):
    return add(ref, "Device:C", fp, value, {"1": a, "2": b}, mpn=mpn, lcsc=lcsc, group=group)


# ------------------------------------------------------------------ 50-pin inputs
# Pin functions follow Antmicro's unified 50-pin CSI connector as the
# baseboard wires it (csi.kicad_sch, J7/J8) and as its OV5640 dual camera
# board uses it: lanes of the first camera on 5-11 with I2C on 39/40, the
# second camera on 23-29 with I2C on 41/42, two SoM GPIOs on 32/34, and
# switched 3.3 V / 5 V on 47-50.
def fifty(ref, side, head_a, head_b, gpio0, gpio1):
    n = {"9": "GND", "12": "GND", "15": "GND", "18": "GND", "27": "GND", "30": "GND", "MP": "GND",
         "5": f"{head_a}_D1_N", "6": f"{head_a}_D1_P", "7": f"{head_a}_D0_N", "8": f"{head_a}_D0_P",
         "10": f"{head_a}_CK_N", "11": f"{head_a}_CK_P",
         "23": f"{head_b}_D1_N", "24": f"{head_b}_D1_P", "25": f"{head_b}_D0_N", "26": f"{head_b}_D0_P",
         "28": f"{head_b}_CK_N", "29": f"{head_b}_CK_P",
         "39": f"I2C_{head_a}_SDA", "40": f"I2C_{head_a}_SCL", "41": f"I2C_{head_b}_SDA", "42": f"I2C_{head_b}_SCL",
         "47": f"+3V3{side}", "48": f"+3V3{side}"}
    if gpio0:
        n["32"] = gpio0
    if gpio1:
        n["34"] = gpio1
    return add(ref, "Connector_Generic_MountingPin:Conn_01x50_MountingPin", "sideline:WE_68715014522", "WE 68715014522",
               n, mpn="68715014522", descr=f"50-pin 0.5 mm FFC from baseboard {'J7' if side == 'A' else 'J8'}",
               group="inputs")


fifty("J1", "A", "FARL", "NEARL", "SOM_FRAME", "IMU_INT1_SOM")
fifty("J2", "B", "FARR", "NEARR", "EXP_INT_SOM", None)

# ------------------------------------------------------------------ camera ports
# Raspberry Pi 5 / CM4 22-pin camera pinout; lanes 2 and 3 are not wired
# because the baseboard gives each camera two lanes.
PORT = {"FARL": "P1", "NEARL": "P2", "FARR": "P3", "NEARR": "P4"}
for h in HEADS:
    add(PORT[h], "Connector_Generic_MountingPin:Conn_01x22_MountingPin",
        "Connector_FFC-FPC:Hirose_FH12-22S-0.5SH_1x22-1MP_P0.50mm_Horizontal", "FH12-22S-0.5SH",
        {"1": "GND", "2": f"{h}_D0_N", "3": f"{h}_D0_P", "4": "GND", "5": f"{h}_D1_N", "6": f"{h}_D1_P", "7": "GND",
         "8": f"{h}_CK_N", "9": f"{h}_CK_P", "10": "GND", "13": "GND", "16": "GND",
         "17": f"{h}_IO0", "18": f"{h}_IO1", "19": "GND", "20": f"I2C_{h}_SCL", "21": f"I2C_{h}_SDA",
         "22": f"{h}_3V3", "MP": "GND"},
        mpn="FH12-22S-0.5SH(55)", descr=f"{HEAD_LABEL[h]} camera, Raspberry Pi 5 pinout", group="ports")

# ------------------------------------------------------------------ head power
SW = {"FARL": "U3", "NEARL": "U4", "FARR": "U5", "NEARR": "U6"}
RAIL = {"FARL": "+3V3A", "NEARL": "+3V3A", "FARR": "+3V3B", "NEARR": "+3V3B"}
cn = 10
for h in HEADS:
    add(SW[h], "Power_Management:TPS22917DBV", "Package_TO_SOT_SMD:SOT-23-6", "TPS22917DBV",
        {"1": RAIL[h], "2": "GND", "3": f"{h}_PWR_EN", "4": f"{h}_CT", "5": f"{h}_QOD", "6": f"{h}_3V3"},
        mpn="TPS22917DBVR", descr=f"{HEAD_LABEL[h]} 3.3 V load switch", group="power")
    C(f"C{cn}", "1u", RAIL[h], "GND", "power"); cn += 1
    C(f"C{cn}", "1n", f"{h}_CT", "GND", "power"); cn += 1
    C(f"C{cn}", "10u", f"{h}_3V3", "GND", "power", fp=C0603); cn += 1
    C(f"C{cn}", "100n", f"{h}_3V3", "GND", "power"); cn += 1
    # Discharge the head's rail when switched off, so a power cycle really resets it.
    R(f"R{50 + HEADS.index(h)}", "100", f"{h}_3V3", f"{h}_QOD", "power")

add("U7", "Regulator_Linear:AP2112K-1.8", "Package_TO_SOT_SMD:SOT-23-5", "AP2112K-1.8",
    {"1": "+3V3A", "2": "GND", "3": "+3V3A", "5": "+1V8"}, mpn="AP2112K-1.8TRG1",
    descr="1.8 V for the sensor side of the sync translators", group="power")
C("C30", "1u", "+3V3A", "GND", "power")
C("C31", "1u", "+1V8", "GND", "power")
C("C32", "10u", "+3V3A", "GND", "power", fp=C0603)
C("C33", "10u", "+3V3B", "GND", "power", fp=C0603)
add("JP1", "Jumper:SolderJumper_3_Bridged12", "Jumper:SolderJumper-3_P1.3mm_Bridged12_RoundedPad1.0x1.5mm", "VIO 1V8/3V3",
    {"1": "+1V8", "2": "VIO_HEAD", "3": "+3V3A"}, descr="Sensor-side sync level: 1.8 V (default) or 3.3 V", group="power")

# ------------------------------------------------------------------ GPIO expanders
# Internal 100 k pull-ups make every I/O high at reset: heads powered and
# enabled, translators isolated (OE high), directions set to slave.
EXP = {"A": ("U1", "FARL", "NEARL", "SYNCA_OE_N", "LED_A"), "B": ("U2", "FARR", "NEARR", "SYNCB_OE_N", "LED_B")}
for side, (ref, ha, hb, oe, led) in EXP.items():
    add(ref, "Interface_Expansion:PCA9555PW", "Package_SO:TSSOP-24_4.4x7.8mm_P0.65mm", "PCA9555PW",
        {"1": "EXP_INT", "2": "GND", "3": "GND", "21": "GND", "22": f"I2C_{ha}_SCL", "23": f"I2C_{ha}_SDA",
         "12": "GND", "24": "+3V3A",
         "4": f"{ha}_PWR_EN", "5": f"{ha}_IO0", "6": f"{ha}_IO1", "7": f"{ha}_DIR",
         "8": f"{hb}_PWR_EN", "9": f"{hb}_IO0", "10": f"{hb}_IO1", "11": f"{hb}_DIR",
         "13": oe, "14": led, **({"15": "IMU_INT1"} if side == "A" else {})},
        mpn="PCA9555PW,118", descr=f"I2C GPIO 0x20 on the {HEAD_LABEL[ha]} bus", group="control")
    C(f"C{40 if side == 'A' else 41}", "100n", "+3V3A", "GND", "control")

R("R1", "10k", "EXP_INT", "+3V3A", "control")
R("R2", "0", "EXP_INT", "EXP_INT_SOM", "control")

# ------------------------------------------------------------------ frame sync
# One head drives XVS/XHS onto a 3.3 V bus and the others follow; or every
# head is a slave and an outside source drives the bus through J3. The
# expander sets each head's direction; a bad setting is limited by the
# series resistors.
TR = {"A": ("U8", "FARL", "NEARL", "SYNCA_OE_N"), "B": ("U9", "FARR", "NEARR", "SYNCB_OE_N")}
rn = 10
for side, (ref, ha, hb, oe) in TR.items():
    nets = {"16": "+3V3A", "15": "VIO_HEAD", "10": "GND", "9": oe,
            "1": f"{ha}_DIR", "2": f"{ha}_DIR", "7": f"{hb}_DIR", "8": f"{hb}_DIR"}
    for bit, (h, sig) in enumerate(((ha, "XVS"), (ha, "XHS"), (hb, "XVS"), (hb, "XHS")), start=1):
        a_pin, b_pin = str(2 + bit), str(15 - bit)
        nets[a_pin] = f"{h}_{sig}_A"
        nets[b_pin] = f"{h}_{sig}_B"
        R(f"R{rn}", "47", f"{h}_{sig}_A", f"{sig}_BUS", "sync"); rn += 1
        R(f"R{rn}", "33", f"{h}_{sig}_B", f"{h}_{sig}", "sync"); rn += 1
    add(ref, "sideline:SN74AVC4T774PW", "Package_SO:TSSOP-16_4.4x5mm_P0.65mm", "SN74AVC4T774PW", nets,
        mpn="SN74AVC4T774PWR", descr=f"sync level shift and direction, {HEAD_LABEL[ha]} + {HEAD_LABEL[hb]}",
        group="sync")
    C(f"C{42 if side == 'A' else 44}", "100n", "+3V3A", "GND", "sync")
    C(f"C{43 if side == 'A' else 45}", "100n", "VIO_HEAD", "GND", "sync")

R("R30", "100k", "XVS_BUS", "GND", "sync")
R("R31", "100k", "XHS_BUS", "GND", "sync")
R("R32", "1k", "XVS_BUS", "SOM_FRAME", "sync")
R("R33", "47", "XVS_BUS", "EXT_XVS", "sync")
R("R34", "47", "XHS_BUS", "EXT_XHS", "sync")

SYNC = {"FARL": "J4", "NEARL": "J5", "FARR": "J6", "NEARR": "J7"}
for i, h in enumerate(HEADS):
    add(SYNC[h], "Connector_Generic_MountingPin:Conn_01x04_MountingPin",
        "Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal", "SH 4-pin",
        {"1": f"{h}_XVS", "2": f"{h}_XHS", "3": "GND", "4": f"{h}_VIO", "MP": "GND"},
        mpn="SM04B-SRSS-TB(LF)(SN)", descr=f"{HEAD_LABEL[h]} sync: XVS, XHS, GND, VIO", group="sync")
    R(f"R{40 + i}", "100", "VIO_HEAD", f"{h}_VIO", "sync")
add("J3", "Connector_Generic_MountingPin:Conn_01x04_MountingPin",
    "Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal", "SH 4-pin",
    {"1": "EXT_XVS", "2": "GND", "3": "EXT_XHS", "4": "GND", "MP": "GND"},
    mpn="SM04B-SRSS-TB(LF)(SN)", descr="External sync in/out, 3.3 V", group="sync")

# ------------------------------------------------------------------ sensors
add("U10", "sideline:ICM-42688-P", "Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y", "ICM-42688-P",
    {"1": "IMU_AD0", "2": "GND", "3": "GND", "4": "IMU_INT1", "5": "+3V3A", "6": "GND", "7": "GND", "8": "+3V3A",
     "9": "IMU_FSYNC", "10": "GND", "11": "GND", "12": "+3V3A", "13": "I2C_FARL_SCL", "14": "I2C_FARL_SDA"},
    mpn="ICM-42688-P", descr="IMU at 0x69; FSYNC tags the sample nearest each frame", group="sensors")
C("C50", "100n", "+3V3A", "GND", "sensors")
C("C51", "1u", "+3V3A", "GND", "sensors")
C("C52", "100n", "+3V3A", "GND", "sensors")
R("R3", "0", "IMU_INT1", "IMU_INT1_SOM", "sensors")
R("R7", "10k", "IMU_AD0", "+3V3A", "sensors")  # AD0 high: 0x69, clear of 0x68 IMUs on camera boards
# Pin 9 wakes as INT2 (open-drain) until firmware makes it FSYNC; the resistor
# keeps a misconfigured IMU from fighting the frame bus.
R("R8", "1k", "XVS_BUS", "IMU_FSYNC", "sensors")
add("U11", "Sensor_Humidity:SHT4x", "Sensor_Humidity:Sensirion_DFN-4_1.5x1.5mm_P0.8mm_SHT4x_NoCentralPad", "SHT45-AD1B",
    {"1": "I2C_FARL_SDA", "2": "I2C_FARL_SCL", "3": "+3V3A", "4": "GND"},
    mpn="SHT45-AD1B-R2", descr="Pod air temperature and humidity at 0x44", group="sensors")
C("C53", "100n", "+3V3A", "GND", "sensors")

# ------------------------------------------------------------------ indicators, test, mechanics
add("D1", "Device:LED", LED0603, "green", {"1": "GND", "2": "LED_A_ANODE"}, mpn="APT1608SGC", group="control")
add("D2", "Device:LED", LED0603, "green", {"1": "GND", "2": "LED_B_ANODE"}, mpn="APT1608SGC", group="control")
add("D3", "Device:LED", LED0603, "green", {"1": "GND", "2": "LED_PWR_ANODE"}, mpn="APT1608SGC", group="control")
R("R4", "1k", "LED_A", "LED_A_ANODE", "control")
R("R5", "1k", "LED_B", "LED_B_ANODE", "control")
R("R6", "2k2", "+3V3A", "LED_PWR_ANODE", "control")

for i, net in enumerate(("XVS_BUS", "XHS_BUS", "+3V3A", "+3V3B", "+1V8", "GND")):
    add(f"TP{i + 1}", "Connector:TestPoint", "TestPoint:TestPoint_Pad_D1.0mm", net, {"1": net}, group="test")
for i in range(3):
    add(f"H{i + 1}", "Mechanical:MountingHole", "MountingHole:MountingHole_2.2mm_M2", "M2", {}, group="test")

# Power flags tell ERC which nets the connectors feed.
FLAGS = ("+3V3A", "+3V3B", "GND", "VIO_HEAD")


def nets() -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for p in PARTS:
        for pin, n in p.nets.items():
            out.setdefault(n, []).append((p.ref, pin))
    return out


if __name__ == "__main__":
    ns = nets()
    print(len(PARTS), "parts,", len(ns), "nets")
    for n, conns in sorted(ns.items()):
        if len(conns) < 2:
            print("single-pin net:", n, conns)
    refs = [p.ref for p in PARTS]
    assert len(refs) == len(set(refs)), "duplicate refs"
