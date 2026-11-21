from io import BytesIO
from typing import NamedTuple

from manifold3d import CrossSection, Error, Manifold, Mesh64
import networkx as nx
import numpy as np
import trimesh


INPUT_TYPES = (".stl", ".obj", ".3mf")


class MeshReport(NamedTuple):
    triangles: int
    vertices: int
    boundary_edges: int
    non_manifold_edges: int
    duplicates: int
    degenerate: int
    shells: int | None
    issues: tuple[str, ...]
    ready: bool


def read_mesh(path):
    if path.suffix.lower() not in INPUT_TYPES:
        raise ValueError("Choose an STL, OBJ, or 3MF model.")
    scene = trimesh.load_scene(path, process=False)
    if path.suffix.lower() == ".3mf" and scene.units:
        scene = scene.convert_units("mm")
    mesh = scene.to_mesh()
    if len(mesh.faces) == 0 or not np.isfinite(mesh.vertices).all():
        raise ValueError("The file must contain a mesh with finite coordinates.")
    mesh.merge_vertices(merge_tex=True, merge_norm=True)
    mesh.remove_unreferenced_vertices()
    return mesh


def check_mesh(mesh):
    counts = np.bincount(mesh.edges_unique_inverse)
    boundary = int(np.count_nonzero(counts == 1))
    non_manifold = int(np.count_nonzero(counts > 2))
    duplicates = int(np.count_nonzero(~mesh.unique_faces()))
    degenerate = int(np.count_nonzero(~mesh.nondegenerate_faces()))
    issues = []
    winding = mesh.is_winding_consistent
    if boundary:
        issues.append("Open edges leave gaps in the surface.")
    if non_manifold:
        issues.append("Some edges are shared by more than two faces.")
    if duplicates or degenerate:
        issues.append("Duplicate or degenerate faces were found.")
    if not winding:
        issues.append("Adjacent faces have inconsistent directions.")

    solid = None
    shells = None
    if not (boundary or non_manifold or duplicates or degenerate) and winding and len(mesh.faces):
        vertices = mesh.vertices - mesh.bounds.mean(axis=0)
        inverted = mesh.volume < 0
        if inverted:
            issues.append("Faces point inward. Repair can correct the orientation.")
        faces = mesh.faces[:, ::-1] if inverted else mesh.faces
        candidate = Manifold(Mesh64(
            vert_properties=np.ascontiguousarray(vertices, dtype=np.float64),
            tri_verts=np.ascontiguousarray(faces, dtype=np.uint64),
        )).simplify()
        if candidate.status() == Error.NoError and candidate.volume() > 0:
            shells = len(candidate.decompose())
            if shells == 1:
                solid = candidate
            else:
                issues.append("Mold generation requires one connected solid without enclosed shells.")
        else:
            issues.append("The geometry engine could not create a closed, positive-volume solid.")
    return MeshReport(
        len(mesh.faces), len(mesh.vertices), boundary, non_manifold, duplicates, degenerate,
        shells, tuple(issues), solid is not None,
    ), solid


def fill_planar_boundaries(mesh):
    counts = np.bincount(mesh.edges_unique_inverse)
    if np.any(counts > 2):
        return
    graph = nx.Graph()
    graph.add_edges_from(mesh.edges_unique[counts == 1].tolist())
    caps = []
    centers = []
    for nodes in nx.connected_components(graph):
        if not 5 <= len(nodes) <= 128 or any(graph.degree(node) != 2 for node in nodes):
            continue
        first = min(nodes)
        loop = [first]
        previous, current = first, min(graph[first])
        while current != first:
            loop.append(current)
            previous, current = current, next(node for node in graph[current] if node != previous)
        points = mesh.vertices[loop]
        centered = points - points.mean(axis=0)
        _, _, basis = np.linalg.svd(centered, full_matrices=False)
        span = np.ptp(points, axis=0).max()
        if np.max(np.abs(centered @ basis[2])) > max(1e-5, span * 0.005):
            continue
        plane = centered @ basis[:2].T
        following = np.roll(plane, -1, axis=0)
        signed_area = float(np.sum(plane[:, 0] * following[:, 1] - plane[:, 1] * following[:, 0]) / 2)
        hull_area = CrossSection.hull_points(plane).area()
        if hull_area <= 0 or not np.isclose(abs(signed_area), hull_area, rtol=1e-7, atol=1e-12):
            continue
        center = len(mesh.vertices) + len(centers)
        centers.append(points.mean(axis=0))
        caps.extend((center, start, end) for start, end in zip(loop, loop[1:] + loop[:1]))
    if caps:
        mesh.vertices = np.vstack((mesh.vertices, centers))
        mesh.faces = np.vstack((mesh.faces, caps))


def repair_mesh(mesh):
    repaired = mesh.copy()
    repaired.merge_vertices(digits_vertex=5, merge_tex=True, merge_norm=True)
    repaired.update_faces(repaired.unique_faces() & repaired.nondegenerate_faces())
    repaired.remove_unreferenced_vertices()
    if len(repaired.faces) < 3:
        raise ValueError("Too few usable faces remain to repair the mesh.")
    trimesh.repair.fill_holes(repaired)
    fill_planar_boundaries(repaired)
    trimesh.repair.fix_normals(repaired, multibody=False)
    return repaired


def raw_preview(mesh):
    # The checker can display open surfaces even when the mold engine rejects them.
    vertices = np.asarray(mesh.vertices - mesh.bounds.mean(axis=0), dtype=np.float32)
    faces = np.ascontiguousarray(mesh.faces, dtype=np.uint32)
    triangles = vertices[faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    smooth = np.zeros_like(vertices)
    for index in range(3):
        np.add.at(smooth, faces[:, index], normals)
    lengths = np.linalg.norm(smooth, axis=1, keepdims=True)
    smooth /= np.where(lengths > 0, lengths, 1)
    return vertices, faces, smooth


def repaired_stl(mesh):
    data = mesh.export(file_type="stl")
    rounded = trimesh.load_mesh(BytesIO(data), file_type="stl")
    if not rounded.is_volume:
        raise ValueError("The repaired mesh could not be represented as a watertight STL.")
    return data
