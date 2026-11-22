from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import ctypes
from io import BytesIO
from itertools import product
from multiprocessing import freeze_support, get_context
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from manifold3d import CrossSection, Error, Manifold, OpType, triangulate
import numpy as np
import trimesh

from preview import Preview, preview_mesh
from mesh_tools import check_mesh, raw_preview, read_mesh, repair_mesh, repaired_stl


AXES = ("X", "Y", "Z")
OPEN_DIRECTIONS = {
    "+X": (0, -90, 0), "-X": (0, 90, 0),
    "+Y": (90, 0, 0), "-Y": (-90, 0, 0),
    "+Z": (0, 0, 0), "-Z": (180, 0, 0),
}
MOLD_TABS = ("Direct Cast", "Open-Face")
VERTICAL_SPLITS = ("+X", "-X", "+Y", "-Y")
OPEN_VIEW = "Brush-on impression"
PAIR_VIEW = "Front and back"
EXPLODED_VIEW = "Exploded mold"
FIT_VIEW = "Fit in back half"
VIEWS = ("Positive model", EXPLODED_VIEW, "Assembled mold", "Front half", "Back half", PAIR_VIEW, FIT_VIEW)
HALF_VIEWS = ("Front half", "Back half")
MODEL_COLOR = (132, 193, 232)
HALF_COLORS = ((105, 208, 187), (239, 176, 114))
EXTERIOR_FACE_BUDGET = 1600
MAX_OFFSET_SECTIONS = 32
worker_source = None
worker_molds = {}


def opposite_direction(direction):
    return ("-" if direction[0] == "+" else "+") + direction[1]


def face_up_scene(halves, direction, wall, side=None, source=None):
    scene = []
    for index, facing in enumerate((opposite_direction(direction), direction)):
        if side is not None and index != side:
            continue
        vertices, faces, normals = halves[index]
        axis = AXES.index(facing[1])
        face = np.zeros(3, dtype=np.float32)
        face[axis] = 1 if facing[0] == "+" else -1
        up = np.array((0, 1, 0) if axis == 2 else (0, 0, 1), dtype=np.float32)
        rotation = np.array((np.cross(up, face), up, face))
        vertices = vertices @ rotation.T
        lower, upper = vertices.min(axis=0), vertices.max(axis=0)
        offset = -(lower + upper) / 2
        if side is None:
            # Leave a full wall thickness of space between the two molds.
            offset[0] = -upper[0] - wall / 2 if index == 0 else -lower[0] + wall / 2
        offset[2] = -upper[2]
        scene.append(((vertices, faces, normals @ rotation.T), offset, HALF_COLORS[index]))
        if source is not None:
            points, triangles, directions = source
            scene.append(((points @ rotation.T, triangles, directions @ rotation.T), offset, MODEL_COLOR))
    return tuple(scene)


def exploded_scene(halves, source, direction, wall):
    axis = AXES.index(direction[1])
    sign = 1 if direction[0] == "+" else -1
    source_axis = source[0][:, axis] * sign
    lower, upper = float(source_axis.min()), float(source_axis.max())
    gap = max(wall, (upper - lower) * 0.2)
    front, back = halves
    offsets = np.zeros((2, 3))
    offsets[:, axis] = (sign * (upper + gap - (front[0][:, axis] * sign).min()),
                        sign * (lower - gap - (back[0][:, axis] * sign).max()))
    return ((front, offsets[0], HALF_COLORS[0]), (source, np.zeros(3), MODEL_COLOR),
            (back, offsets[1], HALF_COLORS[1]))


def load_model(path):
    report, model = check_mesh(read_mesh(path))
    if model is None:
        raise ValueError(" ".join(report.issues))
    return model


def make_sprue(model, top, diameter, above=None):
    _, _, low_z, _, _, high_z = model.bounding_box()
    if above is not None:
        low_z = above
    radius = diameter / 2
    # ponytail: sample horizontal sections. An explicit inlet position would
    # let users target narrow bands missed by the automatic placement.
    fractions = np.r_[0.995, 0.99, np.linspace(0.975, 0.025, 77)]
    for fraction in fractions:
        height = low_z + fraction * (high_z - low_z)
        # Leave a small margin so the entire sprue starts inside the cavity.
        inset = model.slice(height).offset(-radius * 1.01)
        if inset.is_empty():
            continue
        contours = inset.to_polygons()
        triangles = np.concatenate(contours)[triangulate(contours)]
        if len(triangles) == 0:
            continue
        edges = triangles[:, 1:] - triangles[:, :1]
        areas = abs(edges[:, 0, 0] * edges[:, 1, 1] - edges[:, 0, 1] * edges[:, 1, 0])
        x, y = triangles[areas.argmax()].mean(axis=0)
        sprue = Manifold.cylinder(top - height + diameter, radius, circular_segments=48)
        # Avoid coincident edges on symmetric models while staying inside the inset margin.
        nudge = radius * 0.001
        return sprue.rotate((0, 0, 5)).translate((x + nudge, y - nudge, height))
    if above is not None:
        raise ValueError("The silicone fill hole does not fit above the master. Try a smaller diameter or increase the liner thickness.")
    raise ValueError("The pour hole does not fit. Try a smaller diameter or enter 0 to omit it.")


def make_positioned_port(cavity, top, center, diameter, label, master=None):
    left, front, low, right, back, _ = cavity.bounding_box()
    radius = diameter / 2
    x, y = center
    cannot_fit = f"{label} does not fit at that position. Move it or decrease its diameter."
    if x - radius < left or x + radius > right or y - radius < front or y + radius > back:
        raise ValueError(cannot_fit)
    # The channel extends past the exterior. Use the column's cavity
    # intersections to find even shallow pockets at the chosen X/Y position.
    column = Manifold.cylinder(top - low + diameter, radius, circular_segments=48).translate((*center, low))
    if master is not None:
        contact = column ^ master
        if not contact.is_empty():
            low = contact.bounding_box()[5]
    footprint = CrossSection.circle(radius * 1.01, circular_segments=48).translate(center)
    pockets = sorted((column ^ cavity).decompose(), key=lambda part: part.bounding_box()[5], reverse=True)
    for pocket in pockets:
        _, _, bottom, _, _, high = pocket.bounding_box()
        bottom = max(bottom, low)
        if bottom >= high:
            continue
        # ponytail: sample each pocket across its height. Exact fitting along
        # the entire column would also catch openings between samples.
        for fraction in np.linspace(0.995, 0.005, 80):
            height = bottom + fraction * (high - bottom)
            if (footprint - cavity.slice(height)).is_empty():
                return Manifold.cylinder(top - height + diameter, radius, circular_segments=48).translate((*center, height))
    raise ValueError(cannot_fit)


def offset_in_sections(model, padding, budget):
    # Dilation distributes over a union. Limit the face count per native
    # Minkowski operation, then join the overlapping offsets.
    pending = [model]
    pieces = []
    while pending:
        part = pending.pop()
        if part.num_tri() <= budget:
            pieces.append(part)
            continue
        if len(pending) + len(pieces) + 1 >= MAX_OFFSET_SECTIONS:
            return None
        lower, upper = np.array(part.bounding_box()).reshape(2, 3)
        axis = int(np.argmax(upper - lower))
        normal = tuple(int(index == axis) for index in range(3))
        split = lower[axis] + (upper[axis] - lower[axis]) * 0.503
        halves = part.split_by_plane(normal, split)
        if any(half.is_empty() or half.status() != Error.NoError for half in halves):
            return None
        pending.extend(halves)
    cube = Manifold.cube((padding,) * 3, center=True)
    result = Manifold.batch_boolean([piece.minkowski_sum(cube) for piece in pieces], OpType.Add)
    if result.status() == Error.NoError and result.volume() > 0:
        return result
    return None


def mold_exterior(model, wall, thin_gap=False, *, require_contour=False):
    budget = 2 * EXTERIOR_FACE_BUDGET if thin_gap else EXTERIOR_FACE_BUDGET
    if model.num_tri() <= budget:
        return model.minkowski_sum(Manifold.cube((2 * wall,) * 3, center=True)), ""

    # Only simplify the exterior. Increase its offset to cover the possible
    # movement of the simplified surface. The cavity uses the original mesh.
    tolerance = min(0.5, wall / 10)
    reduced = model.simplify(tolerance)
    if 0 < reduced.num_tri() <= budget:
        padding = 2 * (wall + tolerance)
        note = ("The liner's outer surface was simplified within the offset tolerance." if thin_gap else
                "The exterior was simplified for this detailed model.")
        return reduced.minkowski_sum(Manifold.cube((padding,) * 3, center=True)), note

    if reduced.num_tri() <= budget * MAX_OFFSET_SECTIONS:
        outer = offset_in_sections(reduced, 2 * (wall + tolerance), budget)
        if outer is not None:
            uncovered = model - outer
            if uncovered.status() == Error.NoError and uncovered.is_empty():
                return outer, "The detailed outer contour was built in sections."

    if thin_gap:
        raise ValueError("This model is too detailed to keep a thin silicone gap. Reduce the mesh triangle count and try again.")
    if require_contour:
        raise ValueError("This model is too detailed for a contoured impression. Reduce the triangle count and try again.")

    # For dense geometry that cannot be reduced within the tolerance, the
    # convex envelope of the eight translated hulls avoids a large boolean union.
    hull = model.hull()
    outer = Manifold.batch_hull([hull.translate(offset) for offset in product((-wall, wall), repeat=3)])
    return outer, "This detailed model uses a convex exterior that may fill outer recesses."


def add_alignment_keys(shell, halves, axis, split_at, wall, clearance):
    # Work in the split plane's coordinates so the same placement works on every axis.
    to_model = {"X": (0, 90, 0), "Y": (-90, 0, 0), "Z": (0, 0, 0)}[axis]
    local_shell = shell.rotate(tuple(-angle for angle in to_model))
    radius = min(wall / 4, 3)
    depth = min(wall * 0.6, 4)
    margin = min(wall / 10, 0.5)
    socket_radius = radius + clearance
    reserve_radius = socket_radius + margin
    left, front, _, right, back, _ = local_shell.bounding_box()
    no_fit = "Two alignment keys do not fit. Increase the wall thickness, reduce the key clearance, or move the split."
    if 2 * reserve_radius >= min(right - left, back - front):
        raise ValueError(no_fit)
    centers = local_shell.slice(split_at).offset(-reserve_radius * 1.01)

    # Triangles supply interior points even when the mating surface has holes.
    candidates = np.empty((0, 2))
    if not centers.is_empty():
        contours = centers.to_polygons()
        triangles = np.concatenate(contours)[triangulate(contours)]
        edges = triangles[:, 1:] - triangles[:, :1]
        areas = abs(edges[:, 0, 0] * edges[:, 1, 1] - edges[:, 0, 1] * edges[:, 1, 0])
        # ponytail: bound the search on detailed contours. Use explicit key
        # positions if the automatic candidates miss a suitable patch.
        candidates = triangles[np.argsort(-areas, kind="stable")[:48]].mean(axis=1)

    reserve = Manifold.cylinder(depth + clearance + 2 * margin, reserve_radius, circular_segments=48)
    selected = []
    for _ in range(2):
        for x, y in candidates:
            # Check the full socket depth and the material supporting the pin.
            probe = reserve.translate((x, y, split_at - depth - clearance - margin))
            outside = probe - local_shell
            if outside.status() == Error.NoError and outside.is_empty():
                selected.append((x, y))
                break
        else:
            raise ValueError(no_fit)
        distances = np.linalg.norm(candidates - selected[-1], axis=1)
        order = np.argsort(-distances, kind="stable")
        candidates = candidates[order[distances[order] >= 2 * reserve_radius]]

    pin = Manifold.cylinder(depth + margin, radius, circular_segments=48)
    socket = Manifold.cylinder(depth + clearance + margin, socket_radius, circular_segments=48)
    positive, negative = halves
    for x, y in selected:
        positive = positive + pin.translate((x, y, split_at - depth)).rotate(to_model)
        negative = negative - socket.translate((x, y, split_at - depth - clearance)).rotate(to_model)
    return positive, negative


def release_note(cavity, axis, split_at):
    mesh = cavity.to_mesh64()
    triangles = mesh.vert_properties[mesh.tri_verts, :3]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    component = normals[:, axis]
    angular_tolerance = np.linalg.norm(normals, axis=1) * 1e-5
    position_tolerance = cavity.get_tolerance()
    on_front = triangles[:, :, axis].max(axis=1) > split_at + position_tolerance
    on_back = triangles[:, :, axis].min(axis=1) < split_at - position_tolerance
    if np.any(on_front & (component < -angular_tolerance)) or np.any(on_back & (component > angular_tolerance)):
        return "Undercuts can lock rigid halves. Change the split or use a flexible mold for this shape."
    return ""


def build_mold_parts(model, wall, axis, split_percent, diameter, liner_thickness=None, key_clearance=None,
                     pour_center=None, vents=()):
    numbers = (wall, split_percent) if diameter is None else (wall, split_percent, diameter)
    if not np.isfinite(numbers).all():
        raise ValueError("Enter finite numbers for the mold settings.")
    if wall <= 0 or (diameter is not None and diameter < 0) or not 0 < split_percent < 100:
        raise ValueError("Enter a positive wall thickness, a split between 0 and 100%, and a pour diameter of 0 or more.")
    if key_clearance is not None and (not np.isfinite(key_clearance) or key_clearance <= 0):
        raise ValueError("Enter a positive alignment key clearance.")
    if pour_center is not None and not np.isfinite(pour_center).all():
        raise ValueError("Enter finite coordinates for the pour hole.")
    if any(not np.isfinite(vent).all() or vent[2] <= 0 for vent in vents):
        raise ValueError("Enter finite coordinates and positive diameters for the vents.")
    if axis not in AXES and axis not in OPEN_DIRECTIONS:
        raise ValueError("Choose a front direction for the mold.")
    front_is_negative = axis.startswith("-")
    axis = axis[-1]
    axis_index = AXES.index(axis)
    bounds = model.bounding_box()
    lower, upper = bounds[axis_index], bounds[axis_index + 3]
    from_negative = 100 - split_percent if front_is_negative else split_percent
    split_at = lower + (upper - lower) * from_negative / 100
    normal = tuple(int(index == axis_index) for index in range(3))

    cavity = model
    liner = None
    liner_note = ""
    if liner_thickness is not None:
        if not np.isfinite(liner_thickness) or liner_thickness <= 0:
            raise ValueError("Enter a positive silicone liner thickness.")
        cavity, liner_note = mold_exterior(model, liner_thickness, thin_gap=True)
        liner = (cavity - model).split_by_plane(normal, split_at)
    lower_corner, upper_corner = np.asarray(cavity.bounding_box()).reshape(2, 3)
    outer = Manifold.cube(tuple(upper_corner - lower_corner + 2 * wall)).translate(tuple(lower_corner - wall))
    shell = outer - cavity
    top = outer.bounding_box()[5]
    channels = []
    master = model if liner_thickness is not None else None
    port_note = ""
    if diameter != 0:
        sizes = (diameter,)
        if diameter is None:
            largest = min(6, float((upper_corner - lower_corner).min()) / 3)
            sizes = largest, largest / 2, largest / 4
        for size in sizes:
            try:
                if pour_center is None:
                    above = bounds[5] if master is not None else None
                    inlet = make_sprue(cavity, top, size, above=above)
                else:
                    inlet = make_positioned_port(cavity, top, pour_center, size, "The pour hole", master)
            except ValueError:
                if size == sizes[-1]:
                    raise
            else:
                channels.append(inlet)
                break
        if diameter is None:
            port_note = f"Automatic pour hole: {size:.3g} mm."
    for number, (x, y, vent_diameter) in enumerate(vents, 1):
        vent = make_positioned_port(cavity, top, (x, y), vent_diameter, f"Vent {number}", master)
        if any(not (vent ^ channel).is_empty() for channel in channels):
            raise ValueError(f"Vent {number} overlaps another channel. Move it or decrease its diameter.")
        channels.append(vent)
    for channel in channels:
        shell = shell - channel
    halves = shell.split_by_plane(normal, split_at)
    if key_clearance is not None:
        halves = add_alignment_keys(shell, halves, axis, split_at, wall, key_clearance)
    for half in halves:
        if half.status() != Error.NoError or half.volume() <= 0 or len(half.decompose()) != 1:
            raise ValueError("The settings did not produce two solid mold halves. Try a thicker wall or a different split.")
    if diameter == 0:
        port_note = "Add a pour hole before casting with the halves closed."
    note = "\n".join(filter(None, (liner_note, port_note, release_note(cavity, axis_index, split_at))))
    if front_is_negative:
        halves = halves[::-1]
        if liner is not None:
            liner = liner[::-1]
    return halves, note, liner


def build_mold(model, wall, axis, split_percent, diameter, liner_thickness=None, key_clearance=None,
               pour_center=None, vents=()):
    halves, note, _ = build_mold_parts(model, wall, axis, split_percent, diameter, liner_thickness,
                                     key_clearance, pour_center, vents)
    return halves, note


def make_mold(model, wall, axis, split_percent, diameter):
    return build_mold(model, wall, axis, split_percent, diameter)[0]


def export_mold(source, output, wall, axis, split_percent, diameter, liner_thickness=None, key_clearance=None,
                pour_center=None, vents=()):
    halves, _ = build_mold(load_model(source), wall, axis, split_percent, diameter, liner_thickness,
                           key_clearance, pour_center, vents)
    return save_halves(halves, source, output, axis, silicone=liner_thickness is not None)


def validate_open_face(wall, direction, depth_percent):
    if not np.isfinite((wall, depth_percent)).all():
        raise ValueError("Enter finite numbers for the open-face mold.")
    if wall <= 0 or not 0 < depth_percent <= 100 or direction not in OPEN_DIRECTIONS:
        raise ValueError("Use a positive wall thickness, a surface direction, and a depth above 0% through 100%.")


def build_open_face(model, wall, direction, depth_percent):
    validate_open_face(wall, direction, depth_percent)

    # Point the chosen model surface down so its negative opens upward.
    upright = model.rotate(OPEN_DIRECTIONS[opposite_direction(direction)])
    low_z, high_z = upright.bounding_box()[2::3]
    opening = low_z + (high_z - low_z) * depth_percent / 100 if depth_percent < 100 else high_z
    cavity = upright.trim_by_plane((0, 0, -1), -opening)
    no_opening = "A cavity has no open face at this plane. Change the opening plane or direction."
    if cavity.status() != Error.NoError or cavity.volume() <= 0:
        raise ValueError(no_opening)

    undercut = False
    for pocket in cavity.decompose():
        mesh = pocket.to_mesh64()
        triangles = mesh.vert_properties[mesh.tri_verts, :3]
        edge_a = triangles[:, 1, :2] - triangles[:, 0, :2]
        edge_b = triangles[:, 2, :2] - triangles[:, 0, :2]
        # Upward-facing model faces below the cut become downward-facing
        # mold surfaces. A straight approach from above cannot reach them.
        facing_up = edge_a[:, 0] * edge_b[:, 1] > edge_a[:, 1] * edge_b[:, 0]
        at_lip = np.all(np.isclose(triangles[:, :, 2], opening, rtol=0, atol=pocket.get_tolerance()), axis=1)
        if not np.any(facing_up & at_lip):
            raise ValueError(no_opening)
        undercut |= bool(np.any(facing_up & ~at_lip))

    # Offset only the selected contour, then remove everything above the
    # opening plane. Subtract the original surface to retain its detail.
    outer, exterior_note = mold_exterior(cavity, wall, require_contour=True)
    mold = outer.trim_by_plane((0, 0, -1), -opening) - upright
    if mold.status() != Error.NoError or mold.volume() <= 0 or len(mold.decompose()) != 1:
        raise ValueError("The settings did not produce one open shell. Try changing the thickness or opening plane.")
    caution = "Overhangs may make brushing or release harder. Try a lower opening plane or a different direction." if undercut else ""
    note = "\n".join(filter(None, (f"Impression depth: {opening - low_z:.3g} mm", exterior_note, caution)))
    return mold.translate((0, 0, -mold.bounding_box()[2])), note


def stl_bytes(half):
    raw = half.to_mesh64()
    mesh = trimesh.Trimesh(raw.vert_properties[:, :3], raw.tri_verts, process=False)
    data = mesh.export(file_type="stl")
    rounded = trimesh.load_mesh(BytesIO(data), file_type="stl")
    if rounded.is_volume:
        return data
    # STL stores float32 positions. Rounding can weld a sliver's corners or
    # create identical triangles. Remove those faces without moving vertices.
    corners = np.sort(rounded.faces, axis=1)
    distinct = np.all(np.diff(corners, axis=1) > 0, axis=1)
    rounded.update_faces(distinct & rounded.unique_faces())
    if rounded.is_volume:
        return rounded.export(file_type="stl")
    raise ValueError("A mold part lost mesh detail during STL export. Try moving the mold plane or changing the wall thickness.")


def save_halves(halves, source, output, axis, silicone=False):
    part = "brace" if silicone else "mold"
    folder_name = f"{source.stem}_{'silicone_brace' if silicone else 'mold'}"
    if axis in OPEN_DIRECTIONS:
        files = zip((f"{part}_front.stl", f"{part}_back.stl"), halves)
    else:
        files = ((f"{part}_{axis}_{side}.stl", half) for side, half in zip(("positive", "negative"), halves))
    return save_stls(output, folder_name, files)


def save_stls(output, folder_name, files):
    with tempfile.TemporaryDirectory(prefix=".mold-", dir=output) as staging:
        staged = Path(staging)
        for filename, solid in files:
            (staged / filename).write_bytes(stl_bytes(solid))
        destination = output / folder_name
        number = 1
        while destination.exists():
            number += 1
            destination = output / f"{folder_name}_{number}"
        staged.rename(destination)
    return destination


def geometry_job(source_key, action, repaired=False, settings=None, destination=None, open_face=False):
    global worker_source
    source_path = source_key[0]
    revision = source_key, repaired
    if worker_source is None or worker_source[0] != revision:
        current = (worker_source[1] if worker_source is not None and worker_source[0] == (source_key, False)
                   else read_mesh(source_path))
        if repaired:
            current = repair_mesh(current)
        report, solid = check_mesh(current)
        worker_source = revision, current, report, solid
        worker_molds.clear()
    _, mesh, report, model = worker_source
    if action == "check":
        display = preview_mesh(model) if model is not None else raw_preview(mesh)
        return report, display
    if not report.ready:
        raise ValueError("Repair the mesh before exporting or preparing a mold.")
    if action in ("save_repair", "save_positive"):
        if destination.resolve() == source_path.resolve() or (destination.exists() and destination.samefile(source_path)):
            raise ValueError("Choose a new filename for the STL copy.")
        data = stl_bytes(model) if action == "save_positive" else repaired_stl(mesh)
        with tempfile.TemporaryDirectory(prefix=".mesh-", dir=destination.parent) as directory:
            staged = Path(directory) / "model.stl"
            staged.write_bytes(data)
            staged.replace(destination)
        return destination

    silicone = not open_face and settings[4] is not None
    mode = "open_face" if open_face else "brace" if silicone else "direct"
    cached = worker_molds.get(mode)
    if cached is None or cached[0] != settings:
        if open_face:
            mold, note = build_open_face(model, *settings)
            parts, liners = (mold,), None
        else:
            parts, note, liners = build_mold_parts(model, *settings)
        cached = settings, parts, note, liners
        worker_molds[mode] = cached
    _, parts, note, liners = cached
    if action == "export_liners":
        if liners is None:
            raise ValueError("Enable casting around a printed master to save the liner halves.")
        files = zip(("liner_front.stl", "liner_back.stl"), liners)
        return save_stls(destination, f"{source_path.stem}_flexible_liners", files)
    if action == "export":
        if open_face:
            return save_stls(destination, f"{source_path.stem}_brush_on_mold", (("brush_on_mold.stl", parts[0]),))
        return save_halves(parts, source_path, destination, settings[1], silicone=silicone)
    return tuple(preview_mesh(part) for part in parts), note


def style_window(root):
    background, surface = "#e9eef4", "#ffffff"
    text, muted, border = "#162539", "#4c5e74", "#cbd6e2"
    accent = "#096b64"
    root.configure(background=background)
    root.option_add("*TCombobox*Listbox.font", "{Segoe UI} 11")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Segoe UI", 11), foreground=text, background=surface)
    style.configure("TFrame", background=surface, bordercolor=border)
    style.configure("Card.TFrame", background=surface, bordercolor=border, lightcolor=border, darkcolor=border)
    style.configure("TLabel", background=surface, foreground=text)
    style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"), foreground=text)
    style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"), foreground=text)
    style.configure("Muted.TLabel", foreground=muted)
    style.configure("TEntry", padding=(7, 5), fieldbackground=surface, bordercolor=border, insertcolor=text)
    style.configure("TCombobox", padding=(7, 5), fieldbackground=surface,
                    bordercolor=border, arrowcolor=muted, selectforeground=text, selectbackground=surface)
    for control in ("TEntry", "TCombobox"):
        style.map(control, fieldbackground=[("disabled", "#edf1f5"), ("readonly", surface)],
                  bordercolor=[("focus", accent)], foreground=[("disabled", muted)])
    style.configure("TButton", padding=(11, 7), background="#f2f5f8", bordercolor=border,
                    lightcolor="#f2f5f8", darkcolor="#f2f5f8")
    style.map("TButton", background=[("disabled", "#edf1f5"), ("pressed", "#d9e5ec"), ("active", "#e7eff4")],
              bordercolor=[("focus", accent)], foreground=[("disabled", muted)])
    style.configure("Accent.TButton", background=accent, foreground="#ffffff", bordercolor=accent,
                    lightcolor=accent, darkcolor=accent, font=("Segoe UI", 11, "bold"))
    style.map("Accent.TButton", background=[("disabled", "#edf1f5"), ("pressed", "#064943"), ("active", "#075953")],
              foreground=[("disabled", muted)], bordercolor=[("focus", "#054f49")])
    style.configure("Horizontal.TProgressbar", background=accent, troughcolor="#e9eef4",
                    bordercolor="#e9eef4", lightcolor=accent, darkcolor=accent, thickness=4)


class PortsDialog(simpledialog.Dialog):
    def __init__(self, parent, pour_center, vents):
        self.pour_center = pour_center
        self.vents = vents
        super().__init__(parent, title="Pour and vent positions")

    def body(self, frame):
        frame.configure(padx=12, pady=8)
        ttk.Label(frame, text="Use X and Y in millimeters from the model’s center.\nChannels run upward along +Z.",
                  justify="left").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(frame, text="Pour center as X, Y (blank for automatic)").grid(row=1, column=0, sticky="w")
        self.pour_entry = ttk.Entry(frame, width=32)
        self.pour_entry.grid(row=2, column=0, sticky="ew", pady=(3, 8))
        if self.pour_center is not None:
            self.pour_entry.insert(0, ", ".join(map(str, self.pour_center)))
        ttk.Label(frame, text="Vents as X, Y, diameter (one per line)").grid(row=3, column=0, sticky="w")
        self.vent_entry = tk.Text(frame, width=36, height=5, font=("Segoe UI", 11))
        self.vent_entry.grid(row=4, column=0, sticky="ew", pady=(3, 0))
        self.vent_entry.insert("1.0", "\n".join(", ".join(map(str, vent)) for vent in self.vents))
        self.vent_entry.bind("<Tab>", self.move_focus)
        self.vent_entry.bind("<Shift-Tab>", self.move_focus)
        return self.pour_entry

    def buttonbox(self):
        super().buttonbox()
        self.bind("<Return>", self.accept_on_enter)

    def move_focus(self, event):
        control = event.widget.tk_focusPrev() if event.state & 1 else event.widget.tk_focusNext()
        control.focus_set()
        return "break"

    def accept_on_enter(self, event):
        if event.widget is not self.vent_entry:
            self.ok()

    def validate(self):
        def read_coordinates(line, count, error):
            try:
                values = tuple(float(field) for field in line.split(","))
            except ValueError:
                raise ValueError(error) from None
            if len(values) != count or not np.isfinite(values).all() or (count == 3 and values[2] <= 0):
                raise ValueError(error)
            return values

        try:
            pour = self.pour_entry.get().strip()
            center = read_coordinates(pour, 2, "Enter two finite numbers for the pour center.") if pour else None
            vents = []
            for number, line in enumerate(self.vent_entry.get("1.0", "end-1c").splitlines(), 1):
                if line.strip():
                    vents.append(read_coordinates(line, 3, f"Enter X, Y and a positive diameter on vent line {number}."))
        except ValueError as error:
            messagebox.showerror(self.title(), str(error), parent=self)
            return False
        self.result = center, tuple(vents)
        return True


def launch():
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        ctypes.WinDLL("user32").SetProcessDPIAware()
    root = tk.Tk()
    root.title("Casting Mold Maker")
    style_window(root)
    scale = float(root.tk.call("tk", "scaling")) * 0.75
    width_limit = int(root.winfo_screenwidth() * 0.96)
    height_limit = int(root.winfo_screenheight() * 0.90)
    root.minsize(min(round(850 * scale), width_limit), min(round(640 * scale), height_limit))
    width = min(round(1120 * scale), width_limit)
    height = min(round(690 * scale), height_limit)
    root.geometry(f"{width}x{height}")
    text_width = round(345 * scale)
    root.columnconfigure(1, weight=1)
    root.rowconfigure(0, weight=1)
    panel = ttk.Frame(root, style="Card.TFrame", padding=(20, 18), borderwidth=1, relief="solid")
    panel.grid(row=0, column=0, sticky="ns", padx=(16, 12), pady=16)
    panel.columnconfigure(1, weight=1)

    viewer = ttk.Frame(root, style="Card.TFrame", padding=12, borderwidth=1, relief="solid")
    viewer.grid(row=0, column=1, sticky="nsew", padx=(0, 16), pady=16)
    viewer.columnconfigure(2, weight=1)
    viewer.rowconfigure(1, weight=1)
    preview = Preview(viewer)
    preview.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(10, 8))
    view = tk.StringVar(value=VIEWS[0])
    ttk.Label(viewer, text="View", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
    view_selector = ttk.Combobox(viewer, textvariable=view, values=VIEWS[:1], state="readonly", width=24)
    view_selector.grid(row=0, column=1, sticky="w")
    ttk.Button(viewer, text="Reset view", command=lambda: reset_view()).grid(row=0, column=2, sticky="e")
    ttk.Label(viewer, text="Drag to rotate  ·  Right-drag to pan  ·  Scroll to zoom", style="Muted.TLabel").grid(
        row=2, column=0, columnspan=3, sticky="w",
    )
    positive_save = ttk.Button(viewer, text="Save positive STL…", command=lambda: save_positive())
    positive_save.grid(row=3, column=0, columnspan=3, sticky="e", pady=(5, 0))

    source = tk.StringVar()
    status = tk.StringVar(value="Choose an STL, OBJ, or 3MF model.")
    summary = tk.StringVar(value="Load a source model to check its mesh.")
    notebook = ttk.Notebook(panel)
    notebook.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(16, 0))
    notebook.enable_traversal()
    fields = []
    ports = (None, ())
    save_buttons = []
    default_views = (EXPLODED_VIEW, OPEN_VIEW, VIEWS[0])
    selected_views = list(default_views)
    results = {}
    executor = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))
    source_key = None
    report = original_report = source_preview = None
    repaired = False
    revision = 0
    load_pending = False
    timer = None
    job = None
    notice = ""
    source_error = ""
    last_folder = None
    displayed = None
    camera_view = None

    def browse_source():
        path = filedialog.askopenfilename(
            parent=root, title="Choose a model",
            filetypes=[("3D models", "*.stl *.obj *.3mf"), ("All files", "*.*")],
        )
        if path:
            source.set(path)

    def settings_for(index):
        if index >= len(fields):
            return None
        if MOLD_TABS[index] == "Open-Face":
            wall, direction, opening = fields[index]
            settings = float(wall.get()), direction.get(), float(opening.get())
            validate_open_face(*settings)
            return settings
        wall, axis, split, diameter, keys, clearance, around_master, silicone_gap = fields[index]
        wall_mm, split_percent = float(wall.get()), float(split.get())
        pour = diameter.get().strip()
        diameter_mm = None if pour.lower() == "auto" else float(pour)
        key_gap = float(clearance.get()) if keys.get() else None
        liner = float(silicone_gap.get()) if around_master.get() else None
        if not np.isfinite((wall_mm, split_percent)).all() or (diameter_mm is not None and not np.isfinite(diameter_mm)):
            raise ValueError("Enter finite numbers for the mold settings.")
        if wall_mm <= 0 or not 0 < split_percent < 100 or (diameter_mm is not None and diameter_mm < 0):
            raise ValueError("Use a positive wall thickness, a split between 0 and 100%, and Auto or a pour diameter of 0 or more.")
        if key_gap is not None and (not np.isfinite(key_gap) or key_gap <= 0):
            raise ValueError("Enter a positive alignment key clearance.")
        if liner is not None and (not np.isfinite(liner) or liner <= 0):
            raise ValueError("Enter a positive silicone gap around the master.")
        return wall_mm, axis.get(), split_percent, diameter_mm, liner, key_gap, *ports

    def current_settings(index):
        try:
            return settings_for(index), ""
        except ValueError as error:
            return None, str(error) if str(error).startswith(("Enter", "Use")) else "Enter numbers for the mold settings."

    def reset_view():
        mode = view.get()
        if mode == OPEN_VIEW:
            preview.reset(pitch=75)
        elif mode == EXPLODED_VIEW:
            axis = fields[0][1].get()[1]
            preview.reset(yaw={"X": 22, "Y": 68, "Z": 32}[axis], pitch=26)
        elif mode in (PAIR_VIEW, FIT_VIEW) or mode in HALF_VIEWS:
            preview.reset(yaw=0, pitch=65)
        else:
            preview.reset()

    def render():
        nonlocal displayed, camera_view
        index = notebook.index("current")
        settings, invalid = current_settings(index)
        cached = results.get(index)
        meshes, note = cached[1:] if cached is not None and cached[0] == settings else (None, "")
        if meshes is None:
            allowed_views = VIEWS[:1]
        elif len(meshes) == 1:
            allowed_views = (VIEWS[0], OPEN_VIEW)
        else:
            allowed_views = VIEWS
        mode = selected_views[index] if selected_views[index] in allowed_views else VIEWS[0]
        view_selector.configure(values=allowed_views)
        view.set(mode)
        if camera_view != (index, mode):
            camera_view = index, mode
            reset_view()
        scene_key = (revision, repaired, mode, index if meshes is not None else None, id(meshes), id(source_preview))
        if scene_key != displayed:
            displayed = scene_key
            origin = np.zeros(3)
            if mode == VIEWS[0]:
                scene = ((source_preview, origin, MODEL_COLOR),) if source_preview is not None else ()
            elif mode == OPEN_VIEW:
                scene = ((meshes[0], origin, HALF_COLORS[0]),)
            elif mode == EXPLODED_VIEW:
                scene = exploded_scene(meshes, source_preview, settings[1], settings[0])
            elif mode == FIT_VIEW:
                scene = face_up_scene(meshes, settings[1], settings[0], side=1, source=source_preview)
            elif mode == PAIR_VIEW or mode in HALF_VIEWS:
                half = HALF_VIEWS.index(mode) if mode in HALF_VIEWS else None
                scene = face_up_scene(meshes, settings[1], settings[0], side=half)
            else:
                scene = tuple(zip(meshes[:2], (origin, origin), HALF_COLORS))
            preview.show(scene, "Choose a source model to see it here.")

        for tab_index, button in enumerate(save_buttons):
            tab_settings, _ = current_settings(tab_index)
            record = results.get(tab_index)
            can_save = job is None and record is not None and record[0] == tab_settings and record[1] is not None
            button.state(["!disabled"] if can_save else ["disabled"])
            if tab_index == 0:
                can_save_liners = can_save and tab_settings[4] is not None
                liner_save.state(["!disabled"] if can_save_liners else ["disabled"])
        can_repair = job is None and report is not None and bool(report.issues) and not repaired
        repair_button.state(["!disabled"] if can_repair else ["disabled"])
        can_save_repair = job is None and repaired and report is not None and report.ready
        repair_save.state(["!disabled"] if can_save_repair else ["disabled"])
        can_save_positive = job is None and report is not None and report.ready
        positive_save.state(["!disabled"] if can_save_positive else ["disabled"])

        if job is not None:
            status.set(job)
        elif notice:
            status.set(notice)
        elif source_error:
            status.set(source_error)
        elif report is None:
            status.set("Checking the source mesh…" if load_pending else "Choose an STL, OBJ, or 3MF model.")
        elif index >= len(fields):
            status.set("Mesh ready for mold generation." if report.ready else "This mesh needs repair before mold generation.")
        elif not report.ready:
            status.set("Open Mesh Check to inspect and repair the source mesh.")
        elif invalid:
            status.set(invalid)
        elif meshes is not None:
            status.set("Mold preview ready." + (f"\n{note}" if note else ""))
        elif note:
            status.set(note)
        else:
            status.set("Preparing the mold preview…")

    def summarize():
        if report is None:
            summary.set("Load a source model to check its mesh.")
            return
        title = "After repair" if repaired else "Source mesh"
        values = [
            f"{report.triangles:,} triangles · {report.vertices:,} vertices",
            f"Open edges   {report.boundary_edges:,}",
            f"Non-manifold edges   {report.non_manifold_edges:,}",
            f"Duplicate faces   {report.duplicates:,}",
            f"Degenerate faces   {report.degenerate:,}",
        ]
        if report.shells is not None:
            values.append(f"Closed shells   {report.shells:,}")
        if repaired:
            before = original_report
            prior = (before.boundary_edges, before.non_manifold_edges, before.duplicates, before.degenerate)
            values.append(f"Before repair: {prior[0]:,} open edges, {prior[1]:,} non-manifold edges, "
                          f"{prior[2]:,} duplicates, {prior[3]:,} degenerate faces.")
        details = "\n".join(report.issues) if report.issues else "Checks passed."
        summary.set(title + "\n\n" + "\n".join(values) + "\n\n" + details)

    def submit(action, label, settings=None, destination=None, tab_index=None, do_repair=False):
        nonlocal executor, job
        current_revision = revision
        open_face = tab_index is not None and MOLD_TABS[tab_index] == "Open-Face"
        args = (source_key, action, repaired or do_repair, settings, destination, open_face)
        job = label
        progress.grid()
        progress.start(15)
        render()
        try:
            future = executor.submit(geometry_job, *args)
        except BrokenProcessPool:
            executor.shutdown(wait=False, cancel_futures=True)
            executor = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))
            future = executor.submit(geometry_job, *args)

        def finish():
            nonlocal job, executor, report, original_report, source_preview, repaired, notice, source_error
            if not future.done():
                root.after(75, finish)
                return
            job = None
            progress.stop()
            progress.grid_remove()
            if current_revision != revision:
                refresh()
                return
            try:
                result = future.result()
            except Exception as error:
                if isinstance(error, BrokenProcessPool):
                    executor.shutdown(wait=False, cancel_futures=True)
                    executor = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))
                    detail = "The geometry process stopped. Try a simpler model or adjust the mold settings."
                else:
                    detail = str(error) or "The geometry operation failed."
                if action == "preview":
                    results[tab_index] = settings, None, detail
                elif action == "check" and not do_repair:
                    source_error = detail
                else:
                    notice = detail
                    messagebox.showerror("Casting Mold Maker", detail, parent=root)
            else:
                if action == "check":
                    report, source_preview = result
                    repaired = do_repair
                    if not repaired:
                        original_report = report
                    else:
                        results.clear()
                    summarize()
                elif action == "preview":
                    results[tab_index] = settings, *result
                elif action == "export":
                    notice = f"Saved brush-on mold STL in {result}" if open_face else f"Saved two STL files in {result}"
                elif action == "save_positive":
                    notice = f"Saved positive STL to {result}"
                elif action == "export_liners":
                    notice = f"Saved two liner STLs in {result}"
                else:
                    notice = f"Saved repaired STL to {result}"
            refresh()

        root.after(75, finish)

    def refresh():
        nonlocal load_pending, source_key, source_error
        render()
        if job is not None or timer is not None:
            return
        if load_pending:
            load_pending = False
            try:
                path = Path(source.get().strip()).resolve()
                if not path.is_file():
                    raise ValueError("Choose an existing model file.")
                info = path.stat()
                source_key = path, info.st_size, info.st_mtime_ns
            except (OSError, ValueError) as error:
                source_error = str(error)
                render()
                return
            submit("check", "Checking the source mesh…")
            return
        index = notebook.index("current")
        settings, _ = current_settings(index)
        record = results.get(index)
        if report is not None and report.ready and settings is not None and (record is None or record[0] != settings):
            submit("preview", f"Preparing {MOLD_TABS[index].lower()} preview…", settings=settings, tab_index=index)

    def schedule(delay=340):
        nonlocal timer
        if timer is not None:
            root.after_cancel(timer)

        def update():
            nonlocal timer
            timer = None
            refresh()

        timer = root.after(delay, update)

    def source_changed(*_):
        nonlocal revision, report, original_report, source_preview, repaired, notice, source_error, load_pending
        revision += 1
        report = original_report = source_preview = None
        repaired = False
        notice = source_error = ""
        load_pending = bool(source.get().strip())
        results.clear()
        selected_views[:] = default_views
        preview.reset()
        summarize()
        schedule()
        render()

    def settings_changed(*_):
        nonlocal notice
        notice = ""
        schedule()
        render()

    def edit_ports():
        nonlocal ports
        dialog = PortsDialog(root, *ports)
        if dialog.result is not None and dialog.result != ports:
            ports = dialog.result
            settings_changed()

    def tab_changed(_event):
        nonlocal notice
        notice = ""
        refresh()

    def view_changed(_event):
        selected_views[notebook.index("current")] = view.get()
        render()

    def save_mold(index, *, liners=False):
        nonlocal last_folder
        if liners:
            label = "flexible liner STLs"
        else:
            label = "brush-on mold STL" if MOLD_TABS[index] == "Open-Face" else "mold STLs"
        selected = filedialog.askdirectory(
            parent=root, title=f"Choose where to save the {label}",
            initialdir=last_folder or str(source_key[0].parent), mustexist=True,
        )
        if selected:
            last_folder = selected
            action = "export_liners" if liners else "export"
            submit(action, f"Saving {label}…", settings=settings_for(index), destination=Path(selected),
                   tab_index=index)

    def save_repair():
        filename = filedialog.asksaveasfilename(
            parent=root, title="Save repaired mesh as STL", defaultextension=".stl",
            filetypes=[("STL mesh", "*.stl")], initialdir=str(source_key[0].parent),
            initialfile=f"{source_key[0].stem}_repaired.stl",
        )
        if filename:
            submit("save_repair", "Saving the repaired STL…", destination=Path(filename))

    def save_positive():
        filename = filedialog.asksaveasfilename(
            parent=root, title="Save positive model as STL", defaultextension=".stl",
            filetypes=[("STL mesh", "*.stl")], initialdir=str(source_key[0].parent),
            initialfile=f"{source_key[0].stem}_positive.stl",
        )
        if filename:
            submit("save_positive", "Saving the positive STL…", destination=Path(filename))

    ttk.Label(panel, text="Casting Mold Maker", style="Title.TLabel").grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 17),
    )
    ttk.Label(panel, text="Source model").grid(row=1, column=0, sticky="w", padx=(0, 10))
    ttk.Entry(panel, textvariable=source, width=21).grid(row=1, column=1, sticky="ew")
    ttk.Button(panel, text="Browse…", width=8, command=browse_source).grid(row=1, column=2, padx=(8, 0))

    for index, name in enumerate(MOLD_TABS):
        page = ttk.Frame(notebook, padding=(12, 12))
        notebook.add(page, text=name)
        page.columnconfigure(1, weight=1)
        if name == "Open-Face":
            wall = tk.StringVar(value="2")
            direction = tk.StringVar(value="+Z")
            opening = tk.StringVar(value="25")
            fields.append((wall, direction, opening))
            controls = (("Mold thickness (mm)", wall), ("Surface direction", direction), ("Impression depth (%)", opening))
            for row, (label, variable) in enumerate(controls):
                ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
                if variable is direction:
                    field = ttk.Combobox(page, textvariable=direction, values=tuple(OPEN_DIRECTIONS),
                                         width=6, state="readonly")
                else:
                    field = ttk.Entry(page, textvariable=variable, width=10)
                field.grid(row=row, column=1, sticky="w")
                variable.trace_add("write", settings_changed)
            ttk.Label(page, text="Brush material onto the exposed negative impression. Lower the depth for a shallower mold.",
                      wraplength=text_width, style="Muted.TLabel").grid(
                          row=3, column=0, columnspan=2, sticky="w", pady=(8, 12),
                      )
            button = ttk.Button(page, text="Save brush-on mold STL…", style="Accent.TButton",
                                command=lambda tab=index: save_mold(tab))
            button.grid(row=4, column=0, columnspan=2, sticky="w")
            save_buttons.append(button)
            continue
        wall = tk.StringVar(value="5")
        axis = tk.StringVar(value="+Y")
        split = tk.StringVar(value="50")
        diameter = tk.StringVar(value="Auto")
        keys = tk.BooleanVar(value=False)
        clearance = tk.StringVar(value="0.25")
        around_master = tk.BooleanVar(value=False)
        silicone_gap = tk.StringVar(value="3")
        fields.append((wall, axis, split, diameter, keys, clearance, around_master, silicone_gap))
        controls = [("Wall thickness (mm)", wall), ("Front direction", axis), ("Split position (%)", split),
                    ("Pour hole (mm or Auto)", diameter)]
        for row, (label, variable) in enumerate(controls):
            ttk.Label(page, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            if variable is axis:
                field = ttk.Combobox(page, textvariable=variable, values=VERTICAL_SPLITS, width=6, state="readonly")
            else:
                field = ttk.Entry(page, textvariable=variable, width=10)
            field.grid(row=row, column=1, sticky="w")
            variable.trace_add("write", settings_changed)
        row = len(controls)
        ttk.Checkbutton(page, text="Add alignment keys", variable=keys).grid(
            row=row, column=0, sticky="w", pady=4,
        )
        ttk.Button(page, text="Ports…", command=edit_ports).grid(row=row, column=1, sticky="w")
        ttk.Label(page, text="Key clearance (mm)").grid(row=row + 1, column=0, sticky="w", padx=(0, 12), pady=4)
        clearance_entry = ttk.Entry(page, textvariable=clearance, width=10, state="disabled")
        clearance_entry.grid(row=row + 1, column=1, sticky="w")
        clearance.trace_add("write", settings_changed)

        def toggle_keys(*_, enabled=keys, entry=clearance_entry):
            entry.state(["!disabled"] if enabled.get() else ["disabled"])
            settings_changed()

        keys.trace_add("write", toggle_keys)
        ttk.Checkbutton(page, text="Cast silicone around a printed master", variable=around_master).grid(
            row=row + 2, column=0, columnspan=2, sticky="w", pady=4,
        )
        ttk.Label(page, text="Silicone gap (mm)").grid(row=row + 3, column=0, sticky="w", padx=(0, 12), pady=4)
        gap_entry = ttk.Entry(page, textvariable=silicone_gap, width=10, state="disabled")
        gap_entry.grid(row=row + 3, column=1, sticky="w")
        silicone_gap.trace_add("write", settings_changed)

        def toggle_master(*_, enabled=around_master, entry=gap_entry):
            entry.state(["!disabled"] if enabled.get() else ["disabled"])
            settings_changed()

        around_master.trace_add("write", toggle_master)
        ttk.Label(page, text="The box opens at a vertical seam. Auto fits a pour opening to the cavity.",
                  wraplength=text_width, style="Muted.TLabel").grid(
            row=row + 4, column=0, columnspan=2, sticky="w", pady=(8, 12),
        )
        button = ttk.Button(page, text="Save mold STLs…", style="Accent.TButton", command=lambda tab=index: save_mold(tab))
        button.grid(row=row + 5, column=0, sticky="w")
        save_buttons.append(button)
        liner_save = ttk.Button(page, text="Save liner STLs…", command=lambda tab=index: save_mold(tab, liners=True))
        liner_save.grid(row=row + 5, column=1, sticky="w", padx=(8, 0))

    check_tab = ttk.Frame(notebook, padding=(12, 12))
    notebook.add(check_tab, text="Mesh Check")
    repair_button = ttk.Button(check_tab, text="Repair mesh", command=lambda: submit(
        "check", "Repairing the source mesh…", do_repair=True,
    ))
    repair_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
    repair_save = ttk.Button(check_tab, text="Save repaired STL…", command=save_repair)
    repair_save.grid(row=0, column=1, sticky="w")
    ttk.Label(check_tab, textvariable=summary, justify="left", wraplength=text_width).grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(12, 0),
    )
    ttk.Label(panel, textvariable=status, wraplength=text_width, style="Muted.TLabel").grid(
        row=3, column=0, columnspan=3, sticky="w", pady=(12, 0),
    )
    progress = ttk.Progressbar(panel, mode="indeterminate")
    progress.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
    progress.grid_remove()

    source.trace_add("write", source_changed)
    notebook.bind("<<NotebookTabChanged>>", tab_changed)
    view_selector.bind("<<ComboboxSelected>>", view_changed)
    render()
    root.mainloop()
    executor.shutdown()


if __name__ == "__main__":
    freeze_support()
    launch()
