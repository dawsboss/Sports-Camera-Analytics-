// Sideline camera head: four fixed 4K cameras on one mast.
//
// Two "far" cameras (6 mm lenses) look across at the far half; two "near"
// cameras (4 mm lenses) look down at the near half and the flanks. The
// pixels go where the distance is: the far corners are 2-3x farther away
// than the near side, so they get the narrow lenses. The angles come from
// hardware/rig_geometry.py for an 8 m mast 10 m behind the touchline and
// keep the whole pitch in frame from 6 to 8 m high and 8 to 12 m back.
//
// Units mm. Head frame: z up, +y toward the pitch, +x to the right as seen
// standing behind the rig. With a camera's swivel set straight it looks
// along its pad's normal, so the pads carry the aim and the swivels only
// trim. The rig tolerates +-2 deg of aiming error without coverage gaps.
//
// One part per render:  openscad -D 'part="head"' -o head.stl sideline_head.scad
// part = head | hood | collar | guy_ring | pad_test | boss_test | assembly

part = "assembly";

/* [Aim, degrees: yaw from straight across (+ right), tilt down] */
far_yaw   = 25.5;
far_tilt  = 4.0;
near_yaw  = 40.0;
near_tilt = 26.0;

/* [Pad centres, right-hand side; the left side is mirrored] */
far_pos  = [42, 40, 144];
near_pos = [44, 34, 60];

/* [Camera base: Milesight MS-C8164-PD cable-out. Measure yours first.] */
cam_pcd          = 53;    // hole circle, from the datasheet drawing
cam_holes        = 3;
cam_base_d       = 70;    // preview model only
insert_hole_d    = 4.1;   // M3 heat-set insert (M3 x 5.7 class)
insert_depth     = 8;
pad_d            = 78;
pad_t            = 14;    // deep enough for the cable slot under the base
cable_recess_d   = 22;    // under the base centre, where the pigtail leaves
cable_recess_dep = 9;
cable_slot_w     = 14;    // the pigtail leaves downward, on the drip side

/* [Core] */
core_x  = 25;             // half width
core_yf = 26;             // front face
core_yb = -22;            // back face
core_h  = 188;
core_r  = 8;

/* [Mast: 3/8"-16 stud into a captive nut; collar stops the head turning] */
boss_d        = 72;
boss_h        = 30;
stud_d        = 10.2;     // 3/8" stud clearance
nut_af        = 14.7;     // 9/16" across flats plus clearance
nut_t         = 9.0;      // 21/64" nut plus clearance
nut_z         = 3;        // tripod studs are short: keep the nut low
collar_bolt   = 28;       // x of the two M5 bolts from collar to boss
m5_insert_d   = 6.4;      // M5 heat-set insert hole
m5_clear      = 5.5;
mast_tube_d   = 25;       // what the collar grips on YOUR mast: measure it
collar_h      = 34;
collar_flange = 6;

/* [Switch: Ubiquiti USW-Flex, 123 x 107 x 28, ports facing down] */
sw_w      = 107;
sw_h      = 123;
sw_t      = 28;
plate_t   = 6;
plate_z0  = 42;
plate_w   = 140;
strap_x   = 55;           // inner edge of the strap slots, clear of the switch
hood_wall = 2.4;
m3_clear  = 3.4;

$fn = 72;
eps = 0.01;

// ---------------------------------------------------------------- geometry

// A pad's local frame: face at z = 0, camera looking along +z, pad body in
// z < 0. Local -y is world up and local +y world down, for any yaw and tilt.
module aim(yaw, tilt) { rotate([0, 0, -yaw]) rotate([-(90 + tilt), 0, 0]) children(); }

module at_pad(p, yaw, tilt, side) {
    translate([side * p[0], p[1], p[2]]) aim(side * yaw, tilt) children();
}

module pad_disc() { translate([0, 0, -pad_t]) cylinder(d = pad_d, h = pad_t); }

module pad_cuts() {
    // Insert holes: one at the top (local -y), the others 120 deg on.
    for (i = [0 : cam_holes - 1])
        rotate([0, 0, 270 + i * 360 / cam_holes])
            translate([cam_pcd / 2, 0, -insert_depth]) cylinder(d = insert_hole_d, h = insert_depth + 1);
    translate([0, 0, -cable_recess_dep]) cylinder(d = cable_recess_d, h = cable_recess_dep + 1);
    translate([-cable_slot_w / 2, 0, -cable_recess_dep]) cube([cable_slot_w, pad_d, cable_recess_dep + 1]);
}

module core_block() {
    hull() for (x = [-core_x + core_r, core_x - core_r], y = [core_yb + core_r, core_yf - core_r])
        translate([x, y, 0]) cylinder(r = core_r, h = core_h);
}

// Where a pad's web lands on the core: the front face, on the pad's side.
module web_anchor(p, side) {
    translate([side > 0 ? 2 : -core_x, core_yf - 10, p[2] - 30]) cube([core_x - 2, 10, 60]);
}

module pads_and_webs() {
    for (side = [-1, 1], cfg = [[far_pos, far_yaw, far_tilt], [near_pos, near_yaw, near_tilt]])
        hull() {
            at_pad(cfg[0], cfg[1], cfg[2], side) pad_disc();
            web_anchor(cfg[0], side);
        }
}

module switch_plate() {
    translate([-plate_w / 2, core_yb - plate_t, plate_z0]) cube([plate_w, plate_t + 1, sw_h + 16]);
}

// Tether eye at the top back: a cord from here to the mast below the head
// is what saves the cameras if the stud ever lets go.
module tether_eye() {
    translate([0, core_yb + 6, core_h - 2]) rotate([0, 90, 0])
        difference() { cylinder(r = 11, h = 10, center = true); cylinder(r = 5, h = 12, center = true); }
}

// Zip-tie loops down both sides of the core, for the camera pigtails.
module tie_loops() {
    for (side = [-1, 1], z = [40, 100, 160])
        translate([side * (core_x + 2), -8, z])
            difference() { cube([8, 10, 8], center = true); translate([side * 1.5, 0, 0]) cube([3.2, 5.5, 10], center = true); }
}

// Engraved where nothing else lands: the flat of each core side between the
// tie loops, and the top face, which the hood leaves visible.
module labels() {
    for (side = [-1, 1], t = [["FAR", "6mm", 128], ["NEAR", "4mm", 72]], line = [0, 1])
        translate([side * (core_x - 0.7), -3, t[2] + 3.5 - 7 * line]) rotate([90, 0, side * 90])
            linear_extrude(1) text(t[line], size = 4, halign = "center", valign = "center", font = "Liberation Sans:style=Bold");
    translate([0, 0, core_h - 0.7]) linear_extrude(1) {
        translate([0, -2]) text("PITCH", size = 6, halign = "center", valign = "center", font = "Liberation Sans:style=Bold");
        polygon([[-5, 6], [5, 6], [0, 13]]);
    }
}

module head() {
    difference() {
        union() {
            core_block();
            cylinder(d = boss_d, h = boss_h);
            pads_and_webs();
            switch_plate();
            tether_eye();
            tie_loops();
        }
        for (side = [-1, 1]) {
            at_pad(far_pos, far_yaw, far_tilt, side) pad_cuts();
            at_pad(near_pos, near_yaw, near_tilt, side) pad_cuts();
        }
        // Stud bore and a captive nut that slides in from the back.
        translate([0, 0, -eps]) cylinder(d = stud_d, h = nut_z + nut_t + 6);
        translate([0, 0, nut_z]) {
            rotate([0, 0, 30]) cylinder(d = nut_af / cos(30), h = nut_t, $fn = 6);
            translate([-nut_af / 2, -boss_d, 0]) cube([nut_af, boss_d, nut_t]);
        }
        // Collar bolts go into heat-set inserts pressed from below.
        for (x = [-collar_bolt, collar_bolt]) translate([x, 0, -eps]) cylinder(d = m5_insert_d, h = 11);
        // Strap slots for the switch, and hood screw inserts.
        for (z = [plate_z0 + 22, plate_z0 + sw_h - 12], sx = [-1, 1])
            translate([sx > 0 ? strap_x : -strap_x - 6, core_yb - plate_t - 1, z]) cube([6, plate_t + 3, 22]);
        for (z = [plate_z0 + 10, plate_z0 + sw_h + 6], sx = [-1, 1])
            translate([sx * (plate_w / 2 - 4), core_yb - plate_t - 1, z]) rotate([-90, 0, 0]) cylinder(d = insert_hole_d, h = 9);
        labels();
    }
}

// Rain hood over the switch: open at the bottom so the ports face down and
// the cables drip away. Its side walls clear the straps. Prints roof-down.
module hood() {
    iw = 2 * (strap_x + 8);
    id = sw_t + 8;
    ih = sw_h + 16;
    difference() {
        union() {
            translate([-iw / 2 - hood_wall, -id - hood_wall, 0]) cube([iw + 2 * hood_wall, id + hood_wall, ih + hood_wall]);
            for (sx = [-1, 1]) translate([sx > 0 ? plate_w / 2 - 10 : -plate_w / 2, -hood_wall, 0]) cube([10, hood_wall, ih + hood_wall]);
        }
        translate([-iw / 2, -id, -eps]) cube([iw, id + 1, ih]);
        for (z = [10, sw_h + 6], sx = [-1, 1])
            translate([sx * (plate_w / 2 - 4), 1, z]) rotate([90, 0, 0]) cylinder(d = m3_clear, h = hood_wall + 2);
    }
}

// Collar: the flange bolts up into the boss; the slotted sleeve below is
// squeezed onto the mast by a stainless hose clamp, so the head cannot turn
// on its stud in wind. The boss sits on the mast's top cap inside the bore.
module collar() {
    difference() {
        union() {
            translate([0, 0, -collar_flange]) cylinder(d = boss_d, h = collar_flange);
            translate([0, 0, -collar_h]) cylinder(d = mast_tube_d + 7, h = collar_h);
            // Lips that keep the hose clamp from sliding off.
            for (z = [-collar_h, -collar_h + 16]) translate([0, 0, z]) cylinder(d = mast_tube_d + 10, h = 2);
        }
        translate([0, 0, -collar_h - 1]) cylinder(d = mast_tube_d + 0.6, h = collar_h + 2);
        for (a = [45, 135, 225, 315]) rotate([0, 0, a])
            translate([0, -1, -collar_h - 1]) cube([mast_tube_d, 2, collar_h - collar_flange - 3]);
        for (x = [-collar_bolt, collar_bolt]) translate([x, 0, -collar_flange - 1]) cylinder(d = m5_clear, h = collar_flange + 2);
    }
}

// Guy ring: clamps round a mast section with one M5 bolt; three eyelets at
// 120 deg. Set guy_tube_d to the section it clamps. Staked no more than
// about 5 m from the mast, the lines fall steeper than the near cameras'
// lowest ray (51 deg down) and stay out of every view whatever their bearing.
guy_tube_d = 32;
module guy_ring() {
    od = guy_tube_d + 12;
    difference() {
        union() {
            cylinder(d = od, h = 14);
            for (a = [0, 120, 240]) rotate([0, 0, a]) hull() {
                translate([od / 2 - 4, -7, 0]) cube([1, 14, 14]);
                translate([od / 2 + 9, 0, 0]) cylinder(r = 7, h = 14);
            }
            rotate([0, 0, 60]) translate([od / 2 - 3, -9, 0]) cube([14, 18, 14]);        // clamp ears
        }
        translate([0, 0, -1]) cylinder(d = guy_tube_d + 0.5, h = 16);
        for (a = [0, 120, 240]) rotate([0, 0, a]) translate([od / 2 + 9, 0, -1]) cylinder(d = 6.5, h = 16);
        rotate([0, 0, 60]) {
            translate([od / 2 - 8, -1.5, -1]) cube([30, 3, 16]);                         // the split
            translate([od / 2 + 5, 20, 7]) rotate([90, 0, 0]) cylinder(d = m5_clear, h = 40);
        }
    }
}

// Test prints: one camera pad (does the base sit flat, do the screws line
// up?) and the bottom of the boss (does the stud reach the nut?), before
// committing a day of printer time to the head.
module pad_test() {
    difference() {
        translate([0, 0, pad_t]) pad_disc();
        translate([0, 0, pad_t]) pad_cuts();
    }
}
module boss_test() {
    intersection() { head(); translate([-boss_d, -boss_d, 0]) cube([2 * boss_d, 2 * boss_d, nut_z + nut_t + 4]); }
}

// ------------------------------------------------------------ preview only

module camera_model() {
    color("gainsboro") {
        cylinder(d = cam_base_d, h = 10);
        translate([0, 0, 10]) cylinder(d = 34, h = 50);
        translate([0, 0, 60]) cylinder(d = 64, h = 100);
        translate([-36, -38, 60]) cube([72, 8, 104]);          // sunshield, on the camera's top
    }
    color("black") translate([0, 0, 159]) cylinder(d = 22, h = 2);
}

module assembly() {
    color("dimgray") head();
    for (side = [-1, 1]) {
        at_pad(far_pos, far_yaw, far_tilt, side) camera_model();
        at_pad(near_pos, near_yaw, near_tilt, side) camera_model();
    }
    color("white") translate([-sw_w / 2, core_yb - plate_t - sw_t, plate_z0 + 8]) cube([sw_w, sw_t, sw_h]);
    color("darkslategray", 0.9) translate([0, core_yb - plate_t, plate_z0]) hood();
    color("dimgray") collar();
    color("black") translate([0, 0, -250]) cylinder(d = mast_tube_d, h = 250 - 2);
}

if (part == "head") head();
else if (part == "hood") translate([0, 0, sw_h + 16 + hood_wall]) rotate([180, 0, 0]) hood();
else if (part == "collar") rotate([180, 0, 0]) collar();
else if (part == "guy_ring") guy_ring();
else if (part == "pad_test") pad_test();
else if (part == "boss_test") boss_test();
else if (part == "assembly") assembly();
