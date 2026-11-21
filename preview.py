import math
import sys
import tkinter as tk

import numpy as np

from preview_gl import OpenGLPreview


VIEWPORT_COLOR = "#101927"
PREVIEW_FACE_TARGET = 1_000_000


def preview_mesh(solid):
    display = solid
    if solid.num_tri() > PREVIEW_FACE_TARGET:
        lower, upper = np.array(solid.bounding_box()).reshape(2, 3)
        span = max(upper - lower)
        for fraction in (0.00008, 0.00025, 0.0008, 0.0025):
            display = solid.simplify(span * fraction)
            if display.num_tri() <= PREVIEW_FACE_TARGET:
                break
    raw = display.calculate_normals(0, 35).to_mesh64()
    vertices = np.array(raw.vert_properties[:, :3], dtype=np.float32, order="C", copy=True)
    normals = np.array(raw.vert_properties[:, 3:6], dtype=np.float32, order="C", copy=True)
    faces = np.array(raw.tri_verts, dtype=np.uint32, order="C", copy=True)
    return vertices, faces, normals


class Preview(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, background=VIEWPORT_COLOR, highlightthickness=0, width=630, height=480)
        self.scene = ()
        self.center = np.zeros(3)
        self.span = 1
        self.drag_start = (0, 0)
        self.pending_draw = None
        self.renderer = None
        self.use_opengl = sys.platform == "win32"
        self.upload_mesh = True
        self.surface = tk.Frame(self, background=VIEWPORT_COLOR, borderwidth=0, cursor="fleur")
        self.surface.bind("<Map>", lambda event: self.schedule_draw())
        self.surface.bind("<Expose>", lambda event: self.schedule_draw())
        self.surface.bind("<Destroy>", self.release_renderer)
        self.empty_message = "Choose a source model to see it here."
        self.bind("<Configure>", lambda event: self.schedule_draw())
        for target in (self, self.surface):
            target.bind("<ButtonPress-1>", self.start_drag)
            target.bind("<B1-Motion>", self.orbit)
            target.bind("<ButtonPress-3>", self.start_drag)
            target.bind("<B3-Motion>", self.pan_by)
            target.bind("<MouseWheel>", self.zoom_by)
        self.reset()

    def reset(self):
        self.yaw = math.radians(32)
        self.pitch = math.radians(24)
        self.zoom = 1
        self.pan = np.zeros(2)
        self.schedule_draw()

    def show(self, scene, message=""):
        self.upload_mesh = True
        self.scene = scene
        self.empty_message = message
        if scene:
            low = np.min([vertices.min(axis=0) + offset for (vertices, _, _), offset, _ in scene], axis=0)
            high = np.max([vertices.max(axis=0) + offset for (vertices, _, _), offset, _ in scene], axis=0)
            self.center = (low + high) / 2
            self.span = np.linalg.norm(high - low)
        self.schedule_draw()

    def release_renderer(self, _event=None):
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None

    def start_drag(self, event):
        self.drag_start = event.x, event.y

    def orbit(self, event):
        x, y = self.drag_start
        self.yaw -= (event.x - x) * 0.012
        self.pitch = min(1.55, max(-1.55, self.pitch + (event.y - y) * 0.012))
        self.drag_start = event.x, event.y
        self.schedule_draw()

    def pan_by(self, event):
        x, y = self.drag_start
        self.pan += np.array((event.x - x, event.y - y)) / max(1, min(self.winfo_width(), self.winfo_height()))
        self.drag_start = event.x, event.y
        self.schedule_draw()

    def zoom_by(self, event):
        self.zoom = min(24, max(0.3, self.zoom * 1.16 ** (event.delta / 120)))
        self.schedule_draw()

    def schedule_draw(self):
        if self.pending_draw is None:
            self.pending_draw = self.after_idle(self.draw)

    def draw(self):
        self.pending_draw = None
        width, height = self.winfo_width(), self.winfo_height()
        if not self.scene:
            self.surface.place_forget()
            self.delete("all")
            self.create_text(width / 2, height / 2, text=self.empty_message, fill="#c3d0db",
                             font=("Segoe UI", 11), width=max(width - 32, 1))
            return
        if min(width, height) < 2:
            return
        yaw, pitch = self.yaw, self.pitch
        right = np.array([math.cos(yaw), math.sin(yaw), 0])
        facing = np.array([math.sin(yaw) * math.cos(pitch), -math.cos(yaw) * math.cos(pitch), math.sin(pitch)])
        up = np.cross(facing, right)
        if self.use_opengl:
            if not self.surface.winfo_ismapped():
                self.delete("all")
                self.surface.place(relwidth=1, relheight=1)
                return
            try:
                if self.renderer is None:
                    self.renderer = OpenGLPreview(self.surface.winfo_id())
                basis = np.array((right, up, facing))
                scene = self.scene if self.upload_mesh else None
                self.renderer.draw(width, height, basis, self.span, self.zoom, self.pan, scene, self.center)
                self.upload_mesh = False
                return
            except OSError:
                self.release_renderer()
                self.use_opengl = False
                self.surface.place_forget()

        self.delete("all")
        basis = np.column_stack((right, -up, facing))
        short_side = min(width, height)
        scale = 0.85 * short_side * self.zoom / self.span
        screen_center = self.pan * short_side + [width / 2, height / 2]
        if sum(len(faces) for (_, faces, _), _, _ in self.scene) > 24_000:
            for (vertices, _, _), offset, color in self.scene:
                sampled = vertices[::max(1, (len(vertices) + 2499) // 2500)]
                points = ((sampled + offset - self.center) @ basis)[:, :2] * scale + screen_center
                fill = "#%02x%02x%02x" % color
                for x, y in points:
                    self.create_rectangle(x, y, x + 1, y + 1, fill=fill, outline="")
            self.create_text(14, 14, anchor="nw", text="Software preview · sampled points", fill="#c3d0db")
            return
        polygons = []
        for (vertices, faces, _), offset, color in self.scene:
            projected = (vertices + offset - self.center) @ basis
            triangles = vertices[faces]
            normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            lengths = np.linalg.norm(normals, axis=1)
            light = (normals @ facing) / np.where(lengths > 0, lengths, 1)
            front = light > 1e-9
            triangles = projected[faces[front]]
            points = triangles[:, :, :2] * scale + screen_center
            depths = triangles[:, :, 2].mean(axis=1)
            colors = ((0.47 + 0.53 * light[front, None]) * color).astype(int)
            polygons.extend(zip(depths, points.reshape(-1, 6).tolist(), colors.tolist()))
        for _, points, color in sorted(polygons, key=lambda polygon: polygon[0]):
            fill = "#%02x%02x%02x" % tuple(color)
            self.create_polygon(points, fill=fill, outline=fill, width=1)
