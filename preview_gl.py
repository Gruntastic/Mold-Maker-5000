"""Double-buffered OpenGL preview backed by the Windows graphics driver."""

import ctypes as ct
from ctypes import wintypes as wt

import numpy as np


ARRAY_BUFFER = 0x8892
ELEMENT_BUFFER = 0x8893
STATIC_DRAW = 0x88E4
FLOAT = 0x1406


class PixelFormat(ct.Structure):
    _fields_ = [
        ("size", wt.WORD), ("version", wt.WORD), ("flags", wt.DWORD),
        *[(name, wt.BYTE) for name in (
            "pixel_type", "color_bits", "red_bits", "red_shift", "green_bits", "green_shift",
            "blue_bits", "blue_shift", "alpha_bits", "alpha_shift", "accum_bits",
            "accum_red_bits", "accum_green_bits", "accum_blue_bits", "accum_alpha_bits",
            "depth_bits", "stencil_bits", "aux_buffers", "layer_type", "reserved",
        )],
        ("layer_mask", wt.DWORD), ("visible_mask", wt.DWORD), ("damage_mask", wt.DWORD),
    ]


def bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


def bind_extension(gl, name, result, *arguments):
    lookup = bind(gl, "wglGetProcAddress", ct.c_void_p, ct.c_char_p)
    address = lookup(name.encode("ascii"))
    if address in (None, 1, 2, 3, ct.c_void_p(-1).value):
        raise OSError(f"The OpenGL driver does not support {name}.")
    return ct.WINFUNCTYPE(result, *arguments)(address)


class OpenGLPreview:
    def __init__(self, hwnd):
        gl = self.gl = ct.WinDLL("opengl32.dll")
        user = ct.WinDLL("user32.dll")
        gdi = ct.WinDLL("gdi32.dll")
        handle, integer, uint, floating = wt.HANDLE, ct.c_int, ct.c_uint, ct.c_float
        pointer, double = ct.c_void_p, ct.c_double
        pfd = ct.POINTER(PixelFormat)
        get_dc = bind(user, "GetDC", handle, handle)
        self.release_dc = bind(user, "ReleaseDC", integer, handle, handle)
        choose = bind(gdi, "ChoosePixelFormat", integer, handle, pfd)
        set_format = bind(gdi, "SetPixelFormat", wt.BOOL, handle, integer, pfd)
        self.swap = bind(gdi, "SwapBuffers", wt.BOOL, handle)
        self.activate = bind(gl, "wglMakeCurrent", wt.BOOL, handle, handle)
        self.delete_context = bind(gl, "wglDeleteContext", wt.BOOL, handle)
        create = bind(gl, "wglCreateContext", handle, handle)
        self.hwnd = hwnd
        self.dc = get_dc(hwnd)
        self.context = None
        self.mesh_buffers = []
        try:
            if not self.dc:
                raise OSError("No device context for the preview window.")
            descriptor = PixelFormat()
            descriptor.size = ct.sizeof(descriptor)
            descriptor.version = 1
            descriptor.flags = 0x01 | 0x04 | 0x20  # Double buffer, window, OpenGL
            descriptor.color_bits = 24
            descriptor.depth_bits = 24
            pixel_format = choose(self.dc, ct.byref(descriptor))
            if not pixel_format or not set_format(self.dc, pixel_format, ct.byref(descriptor)):
                raise OSError("No suitable OpenGL pixel format.")
            self.context = create(self.dc)
            if not self.context or not self.activate(self.dc, self.context):
                raise OSError("Could not start the OpenGL preview.")

            for name, result, args in (
                ("glClearColor", None, (floating,) * 4),
                ("glEnable", None, (uint,)),
                ("glEnableClientState", None, (uint,)),
                ("glLightfv", None, (uint, uint, ct.POINTER(floating))),
                ("glLightModelfv", None, (uint, ct.POINTER(floating))),
                ("glColor3f", None, (floating,) * 3),
                ("glVertexPointer", None, (integer, uint, integer, pointer)),
                ("glNormalPointer", None, (uint, integer, pointer)),
                ("glDrawElements", None, (uint, integer, uint, pointer)),
                ("glViewport", None, (integer,) * 4),
                ("glClear", None, (uint,)),
                ("glMatrixMode", None, (uint,)),
                ("glLoadIdentity", None, ()),
                ("glLoadMatrixf", None, (pointer,)),
                ("glOrtho", None, (double,) * 6),
                ("glGetString", ct.c_char_p, (uint,)),
                ("glGetError", uint, ()),
            ):
                bind(gl, name, result, *args)
            self.gen_buffers = bind_extension(gl, "glGenBuffers", None, integer, ct.POINTER(uint))
            self.delete_buffers = bind_extension(gl, "glDeleteBuffers", None, integer, ct.POINTER(uint))
            self.bind_buffer = bind_extension(gl, "glBindBuffer", None, uint, uint)
            self.buffer_data = bind_extension(gl, "glBufferData", None, uint, ct.c_ssize_t, pointer, uint)
            self.renderer = (gl.glGetString(0x1F01) or b"OpenGL").decode(errors="replace")
            gl.glClearColor(16 / 255, 25 / 255, 39 / 255, 1)
            for capability in (0x0B71, 0x0B44, 0x0B50, 0x4000, 0x0B57):
                gl.glEnable(capability)  # Depth test, culling, lighting, light 0, color material
            gl.glEnableClientState(0x8074)  # Vertex array
            gl.glEnableClientState(0x8075)  # Normal array
            rgba = floating * 4
            gl.glLightModelfv(0x0B53, rgba(0.46, 0.46, 0.46, 1))
            gl.glLightfv(0x4000, 0x1201, rgba(0.76, 0.76, 0.76, 1))
            gl.glLightfv(0x4000, 0x1203, rgba(-0.37, 0.43, 0.63, 0))
        except Exception:
            self.close()
            raise
        finally:
            self.activate(None, None)

    def upload(self, scene, center):
        for names, _, _ in self.mesh_buffers:
            self.delete_buffers(2, names)
        self.mesh_buffers.clear()
        for (vertices, faces, normals), offset, color in scene:
            packed = np.empty((len(vertices), 6), dtype=np.float32)
            packed[:, :3] = vertices + (offset - center)
            packed[:, 3:] = normals
            names = (ct.c_uint * 2)()
            self.gen_buffers(2, names)
            for target, name, data in ((ARRAY_BUFFER, names[0], packed), (ELEMENT_BUFFER, names[1], faces)):
                self.bind_buffer(target, name)
                self.buffer_data(target, data.nbytes, data.ctypes.data, STATIC_DRAW)
            self.mesh_buffers.append((names, int(faces.size), tuple(channel / 255 for channel in color)))
        if self.gl.glGetError():
            raise OSError("Could not upload the preview mesh.")

    def draw(self, width, height, basis, span, zoom, pan, scene=None, center=None):
        if not self.activate(self.dc, self.context):
            raise OSError("Lost the OpenGL rendering context.")
        gl = self.gl
        try:
            if scene is not None:
                self.upload(scene, center)
            gl.glViewport(0, 0, width, height)
            gl.glClear(0x4000 | 0x0100)  # Color and depth
            gl.glMatrixMode(0x1701)  # Projection
            gl.glLoadIdentity()
            short_side = min(width, height)
            units = span / (1.7 * short_side * zoom)
            x, y = pan * (2 * short_side * units)
            gl.glOrtho(-width * units - x, width * units - x, -height * units + y, height * units + y, -span, span)
            gl.glMatrixMode(0x1700)  # Model-view
            transform = np.eye(4, dtype=np.float32)
            transform[:3, :3] = basis
            transform = np.ascontiguousarray(transform.T)
            gl.glLoadMatrixf(transform.ctypes.data)
            for names, count, color in self.mesh_buffers:
                self.bind_buffer(ARRAY_BUFFER, names[0])
                self.bind_buffer(ELEMENT_BUFFER, names[1])
                gl.glColor3f(*color)
                gl.glVertexPointer(3, FLOAT, 24, None)
                gl.glNormalPointer(FLOAT, 24, 12)
                gl.glDrawElements(0x0004, count, 0x1405, None)  # Triangles, unsigned 32-bit indices
            if not self.swap(self.dc):
                raise OSError("Could not display the OpenGL frame.")
        finally:
            self.activate(None, None)

    def close(self):
        if self.context:
            self.activate(None, None)
            self.delete_context(self.context)
            self.context = None
        self.mesh_buffers.clear()
        if self.dc:
            self.release_dc(self.hwnd, self.dc)
            self.dc = None
