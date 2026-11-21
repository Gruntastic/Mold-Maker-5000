from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import numpy as np
import trimesh
from manifold3d import Manifold

from mold_maker import (OPEN_DIRECTIONS, build_mold, build_mold_parts, build_open_face, export_mold, geometry_job,
                        load_model, mold_exterior, save_halves, stl_bytes)
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
        np.testing.assert_allclose(liner.volume(), (26 * 36 * 46 - 20 * 30 * 40) / 2)
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

        # At 100% a flat-backed model keeps its full depth. The top of the
        # cavity is completely exposed and a full-thickness base remains.
        info = box_path.stat()
        source_key = box_path, info.st_size, info.st_mtime_ns
        settings = (3, "+Z", 100)
        mold, note = build_open_face(model, *settings)
        np.testing.assert_allclose(mold.bounding_box(), [-13, -18, 0, 13, 18, 43])
        assert "Cavity depth: 40 mm" in note and "Undercuts" not in note
        brush_path = Manifold.cube((18, 28, 41)).translate((-9, -14, 3.5))
        assert (mold ^ brush_path).is_empty()
        assert np.isclose(mold.slice(1).area(), 26 * 36)
        assert np.isclose(mold.slice(42).area(), 26 * 36 - 20 * 30)
        meshes, _ = geometry_job(source_key, "preview", settings=settings, open_face=True)
        assert len(meshes) == 1
        result = geometry_job(source_key, "export", settings=settings, destination=path, open_face=True)
        assert result.name == "box_open_face_mold"
        assert {file.name for file in result.iterdir()} == {"open_face_mold.stl"}
        assert trimesh.load_mesh(result / "open_face_mold.stl").is_volume

        for direction in OPEN_DIRECTIONS:
            mold, note = build_open_face(round_model, 2, direction, 50)
            assert stl_bytes(mold) and "Undercuts" not in note
        tapered = Manifold.cylinder(10, 3, 5, circular_segments=16)
        assert "Undercuts" not in build_open_face(tapered, 2, "+Z", 100)[1]
        assert "Undercuts" in build_open_face(tapered, 2, "-Z", 100)[1]
        try:
            build_open_face(round_model, 2, "+Z", 100)
        except ValueError as error:
            assert "no open face" in str(error)
        else:
            raise AssertionError("A closed round tip should not be exported as an open face.")

        torus = path / "dense-torus.stl"
        trimesh.creation.torus(20, 7, major_sections=96, minor_sections=64).export(torus)
        model = load_model(torus)
        for wall, expected in ((5, "simplified"), (1, "sections")):
            halves, note = build_mold(model, wall, "X", 50, 0)
            assert expected in note
            outer, _ = mold_exterior(model, wall)
            assert np.isclose(sum(half.volume() for half in halves), outer.volume() - model.volume())
            result = save_halves(halves, torus, path, "X")
            assert all(trimesh.load_mesh(file).is_volume for file in result.glob("*.stl"))

        dense = path / "very-dense-torus.stl"
        trimesh.creation.torus(17, 4.6, major_sections=384, minor_sections=192).export(dense)
        model = load_model(dense)
        _, preview_faces, _ = preview_mesh(model)
        assert len(preview_faces) == model.num_tri() == 147_456
        halves, note, sleeve = build_mold_parts(model, 3, "X", 50, 0, liner_thickness=2)
        assert "sections" in note and sleeve.volume() > 0
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
