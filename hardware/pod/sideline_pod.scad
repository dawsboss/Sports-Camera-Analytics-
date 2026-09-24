// Sideline pod: the four cameras hidden in one housing, each lens behind a
// flush round window, so the rig reads as one sports camera rather than four
// security cameras on a stick.
//
// Cameras: four Reolink RLC-833A turrets (4K, 3x zoom, IP66). The far pair
// is zoomed to 54 deg and looks across at the far half; the near pair is set
// to 84 deg and looks down at the near half and the flanks. The aims are
// hardware/rig_geometry.py --head reolink-833a, for an 8 m mast 10 m behind
// the touchline; with every camera 2 deg off they still leave no gap inside
// the lines.
//
// The pod is the convex hull of a window disc in front of each camera, the
// camera bases, a roof, a back and a floor ring. Each window is a face of
// that hull: nothing of the pod stands in front of a window's plane, so the
// housing cannot enter a camera's view and the lens sits flush behind it.
// hardware/pod/check_pod.py checks that, the views, and every clearance.
//
// The shell is a rain screen and a sun shade, not a seal: the cameras are
// IP66 on their own, and the pod breathes in through its floor and out
// under its eave. It prints as a cap and four quarters. Every seam is a
// butt joint outside with a tongue behind it; on the level seams the tongue
// rises from the piece below, so water that creeps into a seam runs back
// out, and the roof has no seam at all.
//
// Units mm. Pod frame: z up, +y toward the pitch, +x to the right as seen
// standing behind the rig, origin on the mast axis 66 mm above the floor.
//
// One part per render:  openscad -D 'part="frame_lower"' -o frame_lower.stl sideline_pod.scad
// part = frame_upper | frame_lower | shell_cap | shell_ul | shell_ur | shell_ll
//      | shell_lr | switch_mount | pad_test | window_test | assembly | cutaway
//      | shell_all | frame_all   (the whole shell or frame as one mesh)

use <../head/sideline_head.scad>        // collar(), the mast clamp from the open head

part = "assembly";

/* [Aim, degrees: yaw from straight across (+ right), tilt down] */
far_yaw   = 26;
far_tilt  = 4;
near_yaw  = 39;
near_tilt = 27;

/* [Zoom the aims assume, horizontal degrees (the sheet's range is 94-50)] */
far_hfov  = 54;
near_hfov = 84;

/* [Turret pads, right-hand side; the left side is mirrored] */
far_pos  = [62, 36, 150];
near_pos = [56, 30, 30];

/* [Turret: Reolink RLC-833A, sheet 117.4 x 103.8 mm. Measure yours first.] */
tur_base_d  = 118;
tur_base_h  = 22;
tur_ball_d  = 92;
tur_ball_c  = 58;          // ball centre, from the pad
tur_front   = 104;         // ball front with the camera looking along the pad normal
tur_pcd     = 90;          // base screw circle: MEASURE, the sheet does not give it
tur_holes   = 3;
insert_hole_d = 4.1;       // M3 heat-set insert
pad_d       = 120;
pad_t       = 10;

/* [Windows] */
far_gap   = 15;            // far windows stand 15 mm proud of the ball: a porthole that keeps rain off
near_gap  = 3;
facet_r   = 40;            // flat rim round each window
win_d     = 58;            // lens, its bezel, and +-5 deg of trim
brow      = true;          // drip brow over each window

/* [Shell] */
wall       = 1.6;          // four perimeters; the frame carries the load, the shell only covers
base_clear = 8;            // the pod's sides stand this far outside each turret base
roof       = [66, -40, 74, 230];    // half width, y back, y front, z
back_y     = -42;
back_z     = [-50, 226];
floor_r    = 78;
floor_z    = -66;
mast_hole_d = 116;         // mast, collar, cables, and the air that cools the cameras
split_z    = 86;           // the quarters split here and at x = 0
cap_z      = 196;          // the cap sits on the upper quarters here
lap        = 10;           // how far a tongue reaches behind the next piece's wall
lap_root   = 4;            // how far it runs along its own piece's wall
lap_gap    = 0.5;          // clearance between a tongue and the wall over it; big ASA parts warp
m3_clear   = 3.4;
seam_pilot = 2.5;          // M3 self-tapping (or #4 sheet-metal) screw into each tongue
seam_boss  = 8;            // a boss behind each lap screw gives it more than one wall to bite
seam_z     = [0, 60, 120, 155];    // lap screws on the x = 0 seams, front and back, where both walls face them square
vent_z     = 209;          // exhaust slots under the eave, clear above the cap's tongue

/* [Frame] */
core_x  = 20;
core_yf = 20;
core_yb = -28;
boss_d  = 72;
boss_h  = 30;
stud_d  = 10.2;
nut_af  = 14.7;
nut_t   = 9.0;
nut_z   = 3;
m5_insert_d = 6.4;
m5_clear    = 5.5;
collar_bolt = 28;

$fn = 64;
eps = 0.01;
B = 500;                   // bigger than the pod

// ------------------------------------------------------------ placement

module aim(yaw, tilt) { rotate([0, 0, -yaw]) rotate([-(90 + tilt), 0, 0]) children(); }
module at_cam(p, yaw, tilt, side) { translate([side * p[0], p[1], p[2]]) aim(side * yaw, tilt) children(); }
module each_cam() {
    for (side = [-1, 1]) {
        at_cam(far_pos, far_yaw, far_tilt, side) children(0);
        at_cam(near_pos, near_yaw, near_tilt, side) children(1);
    }
}

// ------------------------------------------------------------ the shell

// The pod's solid, shrunk by `inset` everywhere; the shell is the solid
// minus itself shrunk by the wall. Every generator shrinks along each of
// its own axes, so no part of the wall comes out thinner than `wall`.
module pod_solid(inset = 0) {
    hull() {
        each_cam() {
            translate([0, 0, tur_front + far_gap - inset - 0.01]) cylinder(r = facet_r - inset, h = 0.01);
            translate([0, 0, tur_front + near_gap - inset - 0.01]) cylinder(r = facet_r - inset, h = 0.01);
        }
        // Turret bases and pads, inflated so the pod's sides clear them.
        each_cam() { base_blank(inset); base_blank(inset); }
        translate([-roof[0] + inset, roof[1] + inset, roof[3] - inset - 0.01]) cube([2 * (roof[0] - inset), roof[2] - roof[1] - 2 * inset, 0.01]);
        translate([-roof[0] + inset, back_y + inset, back_z[0] + inset]) cube([2 * (roof[0] - inset), 0.01, back_z[1] - back_z[0] - 2 * inset]);
        translate([0, 0, floor_z + inset]) cylinder(r = floor_r - inset, h = 0.01);
    }
}
module base_blank(inset) {
    translate([0, 0, -pad_t + inset]) cylinder(r = tur_base_d / 2 + base_clear - inset, h = pad_t + tur_base_h + 4 - 2 * inset);
}

module window_cuts() {
    each_cam() {
        translate([0, 0, tur_front - 12]) cylinder(d = win_d, h = far_gap + 30);
        translate([0, 0, tur_front - 12]) cylinder(d = win_d, h = near_gap + 30);
    }
}

// Drip brow: an arc over the top of each window (local -y is up), standing
// 4.5 mm proud. It sits outside every view cone at that distance, and its
// underside is at 45 degrees so it prints without support.
module brows() {
    if (brow) each_cam() {
        translate([0, 0, tur_front + far_gap - 0.5]) brow_arc();
        translate([0, 0, tur_front + near_gap - 0.5]) brow_arc();
    }
}
module brow_arc() {
    rotate([0, 0, 200]) rotate_extrude(angle = 140, $fn = 96)
        translate([win_d / 2 + 1.5, 0]) polygon([[0, 0], [5, 0], [4.5, 4.5]]);
}

// Air in through the floor round the mast, out through slots high at the
// back, under an eave that keeps the rain out of them.
module vents() {
    for (x = [-45, -25, -5, 15, 35]) translate([x, back_y - 8, vent_z]) cube([10, 18, 8]);
    translate([0, 0, floor_z - 1]) cylinder(d = mast_hole_d, h = 20);
}
// The eave is a chevron: its top sheds rain off the back, and its
// underside rises at 45 degrees from the slots, so the cap prints rim-down
// without support under it.
module eave() {
    difference() {
        translate([60, 0, 0]) rotate([0, -90, 0]) linear_extrude(120)
            polygon([[vent_z + 8, back_y + 2], [vent_z + 16, back_y - 8], [vent_z + 20, back_y + 2]]);
        pod_solid(wall);
    }
}

// Down through the roof into the frame's top plate, up through the floor
// into its ring; none of them on a seam.
module shell_screws() {
    for (a = [45, 135, 225, 315]) rotate([0, 0, a]) {
        translate([35, 0, roof[3] - 8]) cylinder(d = m3_clear, h = 12);
        translate([65, 0, floor_z - 1]) cylinder(d = m3_clear, h = 10);
    }
}

// Lap screws on the x = 0 seams, front and back, through the left piece's
// wall into the right piece's tongue behind it.
module seam_holes(d) {
    for (z = seam_z) translate([-lap / 2, 0, z]) rotate([90, 0, 0]) cylinder(d = d, h = 2 * B, center = true);
}

module shell() {
    difference() {
        union() {
            difference() { pod_solid(0); pod_solid(wall); }
            brows();
            eave();
        }
        window_cuts();
        vents();
        shell_screws();
        seam_holes(m3_clear);
    }
}

// Pieces, as [x0, x1, z0, z1] boxes over all y.
function region(p) =
    p == "cap" ? [-B, B, cap_z, B] :
    p == "ul"  ? [-B, 0, split_z, cap_z] :
    p == "ur"  ? [0, B, split_z, cap_z] :
    p == "ll"  ? [-B, 0, -B, split_z] :
    p == "lr"  ? [0, B, -B, split_z] : [-B, B, -B, B];

// Where each piece's tongues run. The right quarters own the seam at x = 0
// and the piece below owns each level seam. Where the seams cross, the
// corner belongs to one piece, and tongues of different pieces stop
// lap_gap short of each other, so no two of them meet behind a wall. The
// tongue at x = 0 stops above the floor, where the frame's ring backs the seam.
function tongues(p) =
    p == "lr" ? [[-lap, lap_root, z0 + 8, split_z + lap], [0, B, split_z - lap_root, split_z + lap]] :
    p == "ll" ? [[-B, -lap - lap_gap, split_z - lap_root, split_z + lap]] :
    p == "ur" ? [[-lap, lap_root, split_z + lap + lap_gap, cap_z + lap], [0, B, cap_z - lap_root, cap_z + lap]] :
    p == "ul" ? [[-B, -lap - lap_gap, cap_z - lap_root, cap_z + lap]] : [];

module box(r) { translate([r[0], -B, r[2]]) cube([r[1] - r[0], 2 * B, r[3] - r[2]]); }

// A tongue is a strip of shell just inside the wall. Along its own piece it
// fuses to the wall; behind the next piece's wall it stands lap_gap clear.
module tongue(p) {
    t = tongues(p);
    if (len(t) > 0) difference() {
        intersection() {
            union() {
                difference() { pod_solid(wall - 0.01); pod_solid(2 * wall + lap_gap); }
                intersection() {
                    difference() { pod_solid(2 * wall + lap_gap - 0.01); pod_solid(2 * wall + lap_gap + 3); }
                    seam_holes(seam_boss);
                }
            }
            union() for (r = t) box(r);
        }
        difference() { box(region("all")); pod_solid(wall + lap_gap); box(region(p)); }
        window_cuts();
        vents();
        seam_holes(seam_pilot);
    }
}

module shell_piece(p) {
    intersection() { shell(); box(region(p)); }
    tongue(p);
}

// ------------------------------------------------------------ the frame

// The frame stands on the shell's floor: its boss bottom is where the
// mast's top cap seats, and the floor ring round it takes the shell's
// floor screws. Everything below the floor (collar, switch box) hangs
// outside the pod.
z0 = floor_z + wall;
top_plate_z = roof[3] - wall - 6;
ring_od = 2 * floor_r - 14;
ring_slot = [37, 57];              // radii of the four cable-and-air slots in the ring

module pad_disc() { translate([0, 0, -pad_t]) cylinder(d = pad_d, h = pad_t); }
module pad_cuts() {
    for (i = [0 : tur_holes - 1]) rotate([0, 0, 270 + i * 360 / tur_holes])
        translate([tur_pcd / 2, 0, -9]) cylinder(d = insert_hole_d, h = 10);
}

module core() {
    r = 8;
    hull() for (x = [-core_x + r, core_x - r], y = [core_yb + r, core_yf - r])
        translate([x, y, z0]) cylinder(r = r, h = top_plate_z + 6 - z0);
}

// Each turret sits on a seat ring held by three round struts to the core.
// The ring's open middle is where the pigtail leaves the base. Solid arms
// were half the frame's weight; struts carry the same load for a fraction.
seat_id = 80;                       // the seat is r 40..60, so any base screw circle 86-114 mm lands on it
strut_d = 16;
strut_angles = [270, 30, 150];      // local; 270 is the top of the seat

function rx(a) = [[1, 0, 0], [0, cos(a), -sin(a)], [0, sin(a), cos(a)]];
function rz(a) = [[cos(a), -sin(a), 0], [sin(a), cos(a), 0], [0, 0, 1]];
function aim_m(yaw, tilt) = rz(-yaw) * rx(-(90 + tilt));
function cam_pt(p, yaw, tilt, side, q) = [side * p[0], p[1], p[2]] + aim_m(side * yaw, tilt) * q;
function clampv(v, lo, hi) = [for (i = [0 : 2]) min(max(v[i], lo[i]), hi[i])];

module seat(p, yaw, tilt, side) {
    at_cam(p, yaw, tilt, side) difference() { pad_disc(); translate([0, 0, -pad_t - 1]) cylinder(d = seat_id, h = pad_t + 2); }
    for (a = strut_angles) {
        r = (pad_d / 2 + seat_id / 2) / 2;
        q = cam_pt(p, yaw, tilt, side, [r * cos(a), r * sin(a), -pad_t / 2]);
        c = clampv(q, [-core_x + 8, core_yb + 8, z0 + 10], [core_x - 8, core_yf - 8, top_plate_z]);
        hull() { translate(q) sphere(d = strut_d); translate(c) sphere(d = strut_d); }
    }
}

// Trimmed to the roof's underside, so the cap's screws pull the roof down
// onto it and no edge of it reaches the back wall.
module top_plate() {
    intersection() {
        translate([0, 0, top_plate_z]) cylinder(d = 90, h = 6);
        pod_solid(wall + 0.3);
    }
}

module floor_ring() {
    difference() {
        translate([0, 0, z0]) cylinder(d = ring_od, h = 6);
        translate([0, 0, z0 - 1]) difference() {
            cylinder(r = ring_slot[1], h = 8);
            cylinder(r = ring_slot[0], h = 8);
            for (a = [45, 135, 225, 315]) rotate([0, 0, a]) translate([0, -6, -1]) cube([ring_slot[1] + 2, 12, 10]);
        }
    }
}

// The frame prints in two halves that bolt together through the core at
// split_z. Each seat goes whole to the half it hangs from (the near pair
// low, the far pair high), so no seat is ever cut between two prints.
module frame_half_box(which) {
    if (which == "upper") translate([-B, -B, split_z]) cube([2 * B, 2 * B, B]);
    else if (which == "lower") translate([-B, -B, -B]) cube([2 * B, 2 * B, B + split_z]);
    else translate([-B, -B, -B]) cube(2 * B);
}

module frame(which = "all") {
    difference() {
        union() {
            intersection() {
                union() {
                    core();
                    translate([0, 0, z0]) cylinder(d = boss_d, h = boss_h);
                    top_plate();
                    floor_ring();
                }
                frame_half_box(which);
            }
            for (side = [-1, 1]) {
                if (which != "lower") seat(far_pos, far_yaw, far_tilt, side);
                if (which != "upper") seat(near_pos, near_yaw, near_tilt, side);
            }
        }
        for (side = [-1, 1]) {
            at_cam(far_pos, far_yaw, far_tilt, side) pad_cuts();
            at_cam(near_pos, near_yaw, near_tilt, side) pad_cuts();
        }
        // Mast: 3/8"-16 stud into a captive nut that slides in from the back.
        translate([0, 0, z0 - eps]) cylinder(d = stud_d, h = nut_z + nut_t + 6);
        translate([0, 0, z0 + nut_z]) {
            rotate([0, 0, 30]) cylinder(d = nut_af / cos(30), h = nut_t, $fn = 6);
            translate([-nut_af / 2, -boss_d, 0]) cube([nut_af, boss_d, nut_t]);
        }
        for (x = [-collar_bolt, collar_bolt]) translate([x, 0, z0 - eps]) cylinder(d = m5_insert_d, h = 11);
        // Inserts for the shell's screws: the cap's four into the top plate,
        // the lower quarters' four into the floor ring.
        for (a = [45, 135, 225, 315]) rotate([0, 0, a]) {
            translate([35, 0, top_plate_z + 6 - 9]) cylinder(d = insert_hole_d, h = 10);
            translate([65, 0, z0 - eps]) cylinder(d = insert_hole_d, h = 8);
        }
        // The halves bolt together through the core: four M5 dropped down
        // tunnels from the top plate into inserts below the split.
        for (x = [-core_x + 8, core_x - 8], y = [core_yb + 8, core_yf - 8]) {
            translate([x, y, split_z - 12]) cylinder(d = m5_insert_d, h = 12);
            translate([x, y, split_z - eps]) cylinder(d = m5_clear, h = 41);
            translate([x, y, split_z + 40]) cylinder(d = 10, h = top_plate_z + 7 - split_z - 40);
        }
    }
}

// ------------------------------------------------------------ switch box

// The USW-Flex rides on the mast just under the pod, ports down, under the
// open head's hood. A saddle with a shallow V seats against the mast and a
// stainless hose clamp through the saddle holds it; the switch straps to
// the plate behind. Part frame: the mast axis is at y = saddle_y.
sw_w = 107; sw_h = 123; sw_t = 28;
mount_w = 140; mount_h = 139;
saddle_y = 30;                     // mast axis, in front of the plate
module switch_mount() {
    difference() {
        union() {
            translate([-mount_w / 2, -6, 0]) cube([mount_w, 6, mount_h]);
            translate([-22, -1, 0]) cube([44, saddle_y - 8, mount_h]);    // saddle
        }
        // V seat, 90 deg, opening toward the mast: a 32 mm tube touching both
        // flats sits on the saddle axis; anything from 20 to 45 mm sits true.
        translate([0, saddle_y - 16 * sqrt(2), -1]) rotate([0, 0, 45]) cube([60, 60, mount_h + 2]);
        for (z = [20, mount_h - 36]) translate([-30, 4, z]) cube([60, 6, 16]);   // clamp band, behind the seat
        for (z = [22, sw_h - 12], sx = [-1, 1])
            translate([sx > 0 ? 55 : -61, -7, z]) cube([6, 8, 22]);            // switch straps
        for (z = [10, sw_h + 6], sx = [-1, 1])
            translate([sx * (mount_w / 2 - 4), -7, z]) rotate([-90, 0, 0]) cylinder(d = insert_hole_d, h = 8);
    }
}

// ------------------------------------------------------------ tests

module pad_test() { difference() { translate([0, 0, pad_t]) pad_disc(); translate([0, 0, pad_t]) pad_cuts(); } }

// One window's rim, to check that your turret's lens and bezel sit in it.
module window_test() {
    difference() {
        cylinder(r = facet_r + 4, h = wall);
        translate([0, 0, -1]) cylinder(d = win_d, h = wall + 2);
    }
    translate([0, 0, wall - 0.5]) brow_arc();
}

// ------------------------------------------------------------ preview

module turret_model() {
    color("white") cylinder(d = tur_base_d, h = tur_base_h);
    color("whitesmoke") translate([0, 0, tur_ball_c]) sphere(d = tur_ball_d);
    color("black") translate([0, 0, tur_front - 3]) cylinder(d = 26, h = 3);
}

module assembly(cutaway = false) {
    color("dimgray") frame();
    each_cam() { turret_model(); turret_model(); }
    color("white", cutaway ? 0.35 : 1) shell();
    color("dimgray") translate([0, 0, z0]) collar();
    color("white") translate([0, -saddle_y, z0 - 60 - mount_h]) switch_mount();
    color("black") translate([0, 0, z0 - 400]) cylinder(d = 25, h = 398);
}

if (part == "frame_upper") frame("upper");
else if (part == "frame_lower") frame("lower");
else if (part == "frame_all") frame();
else if (part == "shell_cap") shell_piece("cap");
else if (part == "shell_ul") shell_piece("ul");
else if (part == "shell_ur") shell_piece("ur");
else if (part == "shell_ll") shell_piece("ll");
else if (part == "shell_lr") shell_piece("lr");
else if (part == "shell_all") for (p = ["cap", "ul", "ur", "ll", "lr"]) shell_piece(p);
else if (part == "switch_mount") switch_mount();
else if (part == "pad_test") pad_test();
else if (part == "window_test") window_test();
else if (part == "cutaway") assembly(true);
else if (part == "assembly") assembly();
