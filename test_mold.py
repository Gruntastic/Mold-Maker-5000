from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import numpy as np
import trimesh
from manifold3d import Manifold

from mold_maker import (OPEN_DIRECTIONS, build_mold, build_mold_parts, build_open_face, exploded_scene, export_mold,
                        face_up_scene, geometry_job, load_model, save_halves, stl_bytes)
from mesh_tools import check_mesh, read_mesh, repair_mesh
from preview import preview_mesh


def check_export():
    with TemporaryDirectory() as directory:
        path = Path(directory)
        box = trimesh.creation.box(extents=(20, 30, 40))
        for extension in ("stl", "obj", "3mf"):
            source = path / f"box.{extension}"
            if extension == "3mf":
                # Encode the same box in centimeters to exercise unit conversion.
                box_cm = box.copy().apply_scale(0.1)
                draft = path / "draft.3mf"
                draft.write_bytes(box_cm.export(file_type="3mf"))
                with ZipFile(draft) as original, ZipFile(source, "w") as final:
                    for name in original.namelist():
                        data = original.read(name)
                        if name.endswith(".model"):
                            data = data.replace(b'unit="millimeter"', b'unit="centimeter"')
                        final.writestr(name, data)
            else:
                box.export(source)
            original = source.read_bytes()
            result = export_mold(source, path, 5, "Z", 50, 6)
            halves = [trimesh.load_mesh(result / f"mold_Z_{side}.stl")
                      for side in ("positive", "negative")]
            assert all(half.is_volume for half in halves)
            np.testing.assert_allclose(halves[0].bounds, [[-15, -20, 0], [15, 20, 25]])
            np.testing.assert_allclose(halves[1].bounds, [[-15, -20, -25], [15, 20, 0]])
            assert abs(halves[1].volume - 18_000) < 0.01
            assert 17_850 < halves[0].volume < 17_870
            assert source.read_bytes() == original

        sphere = path / "sphere.stl"
        trimesh.creation.icosphere(subdivisions=1, radius=15).export(sphere)
        for axis in ("X", "Y", "Z"):
            result = export_mold(sphere, path, 5, axis, 50, 6)
            assert all(trimesh.load_mesh(file).is_volume for file in result.glob("*.stl"))

        round_model = load_model(sphere)
        for liner_thickness in (None, 3):
            for axis in ("X", "Y", "Z"):
                flat, _ = build_mold(round_model, 5, axis, 40, 4, liner_thickness)
                keyed, _ = build_mold(round_model, 5, axis, 40, 4, liner_thickness, 0.25)
                pins = keyed[0] - flat[0]
                sockets = flat[1] - keyed[1]
                assert len(pins.decompose()) == len(sockets.decompose()) == 2
                assert pins.volume() < sockets.volume()
                assert abs((pins - flat[1]).volume()) < 1e-8
                assert abs((pins - sockets).volume()) < 1e-8
                assert abs((keyed[0] ^ keyed[1]).volume()) < 1e-8
                result = export_mold(sphere, path, 5, axis, 40, 4, liner_thickness, key_clearance=0.25)
                assert all(trimesh.load_mesh(file).is_volume for file in result.glob("*.stl"))
        _, narrow = build_mold(round_model, 5, "Z", 50, 0, key_clearance=0.25)[0]
        _, wide = build_mold(round_model, 5, "Z", 50, 0, key_clearance=0.5)[0]
        assert wide.volume() < narrow.volume()
        try:
            build_mold(round_model, 5, "Z", 50, 0, key_clearance=50)
        except ValueError as error:
            assert "alignment keys do not fit" in str(error)
        else:
            raise AssertionError("Oversized alignment sockets should be rejected.")

        box_path = path / "box.stl"
        model = load_model(box_path)
        closed, _, liner = build_mold_parts(model, 5, "Z", 50, 0, 3)
        poured, _, _ = build_mold_parts(model, 5, "Z", 50, 6, 3)
        np.testing.assert_allclose([half.volume() for half in liner], (26 * 36 * 46 - 20 * 30 * 40) / 2)
        assert liner[0].bounding_box()[2] == liner[1].bounding_box()[5] == 0
        assert all(abs((half ^ model).volume()) < 1e-8 for half in liner)
        np.testing.assert_allclose(poured[0].bounding_box(), [-18, -23, 0, 18, 23, 28])
        assert poured[0].volume() < closed[0].volume()
        assert np.isclose(poured[1].volume(), closed[1].volume())
        assert (poured[0] ^ model).is_empty()
        result = export_mold(box_path, path, 5, "Z", 50, 6, liner_thickness=3)
        assert result.name == "box_silicone_brace"
        assert {file.name for file in result.iterdir()} == {"brace_Z_positive.stl", "brace_Z_negative.stl"}
        assert all(trimesh.load_mesh(file).is_volume for file in result.glob("*.stl"))

        for liner_thickness in (None, 3):
            plain, _ = build_mold(model, 5, "X", 50, 0, liner_thickness)
            vents = ((6, 7, 2), (6, -7, 1))
            center = (-6, -7)
            ported, _ = build_mold(model, 5, "X", 50, 4, liner_thickness, 0.25, center, vents)
            for x, y, port_diameter in ((*center, 4), *vents):
                probe = Manifold.cylinder(15, port_diameter * 0.45).translate((x, y, 19))
                assert ((plain[0] + plain[1]) ^ probe).volume() > 0
                assert ((ported[0] + ported[1]) ^ probe).volume() < 1e-8
            result = save_halves(ported, box_path, path, "X", silicone=liner_thickness is not None)
            assert all(trimesh.load_mesh(file).is_volume for file in result.glob("*.stl"))

        for center, vents, expected in (
            ((13, 0), (), "pour hole does not fit"),
            ((-6, -7), ((-6, -7, 2),), "Vent 1 overlaps"),
            ((-6, -7), ((20, 20, 2),), "Vent 1 does not fit"),
        ):
            try:
                build_mold(model, 5, "X", 50, 4, pour_center=center, vents=vents)
            except ValueError as error:
                assert expected in str(error)
            else:
                raise AssertionError("Invalid port placements should be rejected.")

        info = box_path.stat()
        source_key = box_path, info.st_size, info.st_mtime_ns
        settings = (5, "-Y", 35, 0, None, None, None, ())
        front, back = build_mold(model, *settings)[0]
        np.testing.assert_allclose(front.bounding_box(), [-15, -20, -25, 15, 4.5, 25])
        np.testing.assert_allclose(back.bounding_box(), [-15, 4.5, -25, 15, 20, 25])
        assert np.isclose(front.volume() + back.volume(), 30 * 40 * 50 - model.volume())
        meshes, _ = geometry_job(source_key, "preview", settings=settings)
        left, right = face_up_scene(meshes, "-Y", 5)
        assert left[0][0][:, 0].max() + left[1][0] < right[0][0][:, 0].min() + right[1][0]
        assert all(np.isclose(mesh[0][:, 2].max() + offset[2], 0) for mesh, offset, _ in (left, right))
        for original_half, face_up in zip(meshes, (left, right)):
            np.testing.assert_array_equal(original_half[0][:, 2], face_up[0][0][:, 1])
        result = geometry_job(source_key, "export", settings=settings, destination=path)
        assert {file.name for file in result.iterdir()} == {"mold_front.stl", "mold_back.stl"}
        assert all(trimesh.load_mesh(file).is_volume for file in result.glob("*.stl"))

        source_preview = geometry_job(source_key, "check")[1]
        seated = face_up_scene(meshes, "-Y", 5, side=1, source=source_preview)
        np.testing.assert_array_equal(seated[0][1], seated[1][1])
        auto, note = build_mold(model, 5, "+Y", 50, None)
        assert "Automatic pour hole:" in note and "Undercuts" not in note
        assert (auto[0] + auto[1]).slice(24).area() < 30 * 40
        _, note = build_mold(round_model, 5, "+Y", 65, 0)
        assert "Undercuts" in note

        dome = (Manifold.sphere(12, 32) - Manifold.sphere(9, 32)).trim_by_plane((0, 1, 0), 0)
        front, back = build_mold_parts(dome, 5, "+Y", 50, 0, follow_contour=True)[0]
        ray = (0.2, -10, 0.3), (0.2, 20, 0.3)
        inner, outer = (h.position[1] for h in dome.ray_cast(*ray))
        assert np.isclose(front.ray_cast(*ray)[0].position[1], outer)
        assert np.isclose(back.ray_cast(*ray)[-1].position[1], inner)
        assert all(stl_bytes(half) for half in (front, back))
        for liner_thickness in (None, 3):
            settings = (5, "-Y", 35, 0, liner_thickness, 0.25, None, ())
            meshes, _ = geometry_job(source_key, "preview", settings=settings)
            front_view, center, back_view = exploded_scene(meshes, source_preview, "-Y", 5)
            assert center[0] is source_preview
            assert front_view[0][0][:, 1].max() + front_view[1][1] < source_preview[0][:, 1].min()
            assert back_view[0][0][:, 1].min() + back_view[1][1] > source_preview[0][:, 1].max()
        front, back = build_mold(model, *settings)[0]
        shell = front + back
        inside_gap = Manifold.cube((1, 1, 1)).translate((11, 0, 0))
        outside_gap = inside_gap.translate((3, 0, 0))
        assert (shell ^ inside_gap).is_empty()
        assert np.isclose((shell ^ outside_gap).volume(), 1)
        result = geometry_job(source_key, "export", settings=settings, destination=path)
        assert {file.name for file in result.iterdir()} == {"brace_front.stl", "brace_back.stl"}
        assert all(trimesh.load_mesh(file).is_volume for file in result.glob("*.stl"))
        result = geometry_job(source_key, "export_liners", settings=settings, destination=path)
        assert {file.name for file in result.iterdir()} == {"liner_front.stl", "liner_back.stl"}
        assert all(trimesh.load_mesh(file).is_volume for file in result.glob("*.stl"))

        original = box_path.read_bytes()
        positive_path = geometry_job(source_key, "save_positive", destination=path / "box_positive.stl")
        positive = trimesh.load_mesh(positive_path)
        assert positive.is_volume and np.isclose(positive.volume, model.volume())
        np.testing.assert_allclose(positive.bounds.ravel(), model.bounding_box())
        assert box_path.read_bytes() == original

        settings = (3, "+Z", 100)
        impression, note = build_open_face(model, *settings)
        np.testing.assert_allclose(impression.bounding_box(), [-13, -18, 0, 13, 18, 43])
        assert "Impression depth: 40 mm" in note
        assert np.isclose(impression.slice(42).area(), 26 * 36 - 20 * 30)
        meshes, _ = geometry_job(source_key, "preview", settings=settings, open_face=True)
        assert len(meshes) == 1
        result = geometry_job(source_key, "export", settings=settings, destination=path, open_face=True)
        assert result.name == "box_brush_on_mold"
        assert {file.name for file in result.iterdir()} == {"brush_on_mold.stl"}
        assert trimesh.load_mesh(result / "brush_on_mold.stl").is_volume

        for direction in OPEN_DIRECTIONS:
            impression, _ = build_open_face(round_model, 2, direction, 25)
            assert stl_bytes(impression)
            probe = Manifold.cylinder(12, 0.5).translate((0, 0, 2.25))
            assert (impression ^ probe).is_empty()
        tapered = Manifold.cylinder(10, 3, 5, circular_segments=16)
        front_impression = build_open_face(tapered, 2, "+Z", 25)[0]
        back_impression = build_open_face(tapered, 2, "-Z", 25)[0]
        assert front_impression.bounding_box()[3] > back_impression.bounding_box()[3]
        info = sphere.stat()
        meshes, note = geometry_job((sphere, info.st_size, info.st_mtime_ns), "preview",
                                    settings=(2, "+Z", 85), open_face=True)
        assert len(meshes) == 1 and "Overhangs" in note
        try:
            build_open_face(round_model, 2, "+Z", 100)
        except ValueError as error:
            assert "no open face" in str(error)
        else:
            raise AssertionError("A closed round tip should not be exported as an impression.")

        torus = path / "dense-torus.stl"
        trimesh.creation.torus(20, 7, major_sections=96, minor_sections=64).export(torus)
        model = load_model(torus)
        lower, upper = np.array(model.bounding_box()).reshape(2, 3)
        for wall in (5, 1):
            halves, _ = build_mold(model, wall, "X", 50, 0)
            outer_volume = np.prod(upper - lower + 2 * wall)
            assert np.isclose(sum(half.volume() for half in halves), outer_volume - model.volume())
            corner = Manifold.cube((wall / 2,) * 3).translate(tuple(upper))
            assert np.isclose((halves[0] ^ corner).volume(), corner.volume())
            result = save_halves(halves, torus, path, "X")
            assert all(trimesh.load_mesh(file).is_volume for file in result.glob("*.stl"))

        dense = path / "very-dense-torus.stl"
        trimesh.creation.torus(17, 4.6, major_sections=384, minor_sections=192).export(dense)
        model = load_model(dense)
        _, preview_faces, _ = preview_mesh(model)
        assert len(preview_faces) == model.num_tri() == 147_456
        halves, note, sleeve = build_mold_parts(model, 3, "X", 50, 0, liner_thickness=2)
        assert "sections" in note and all(half.volume() > 0 for half in sleeve)
        assert (halves[0] ^ model).is_empty()
        result = save_halves(halves, dense, path, "X", silicone=True)
        assert all(trimesh.load_mesh(file).is_volume for file in result.glob("*.stl"))


if __name__ == "__main__":
    check_export()
    with TemporaryDirectory() as directory:
        path = Path(directory) / "open.stl"
        cylinder = trimesh.creation.cylinder(12, 20, sections=16)
        cylinder.update_faces(cylinder.face_normals[:, 2] < 0.99)
        cylinder.export(path)
        original = path.read_bytes()
        mesh = read_mesh(path)
        report, model = check_mesh(mesh)
        assert report.boundary_edges == 16 and model is None
        repaired, model = check_mesh(repair_mesh(mesh))
        assert repaired.ready and model.volume() > 0 and path.read_bytes() == original
    print("Mold export checks passed.")
