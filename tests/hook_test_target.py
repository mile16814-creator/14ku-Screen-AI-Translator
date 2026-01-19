import sys
import os
import ctypes
import tkinter as tk
from tkinter import messagebox

# Define Windows types and constants
LPCWSTR = ctypes.c_wchar_p
HDC = ctypes.c_void_p
HWND = ctypes.c_void_p
BOOL = ctypes.c_int
UINT = ctypes.c_uint
INT = ctypes.c_int
RECT = ctypes.c_void_p # Simplified

ETO_GLYPH_INDEX = 0x0010

# Load DLLs
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# Function prototypes
GetDC = user32.GetDC
GetDC.argtypes = [HWND]
GetDC.restype = HDC

ReleaseDC = user32.ReleaseDC
ReleaseDC.argtypes = [HWND, HDC]
ReleaseDC.restype = INT

TextOutW = gdi32.TextOutW
TextOutW.argtypes = [HDC, INT, INT, LPCWSTR, INT]
TextOutW.restype = BOOL

DrawTextW = user32.DrawTextW
DrawTextW.argtypes = [HDC, LPCWSTR, INT, RECT, UINT]
DrawTextW.restype = INT

ExtTextOutW = gdi32.ExtTextOutW
ExtTextOutW.argtypes = [HDC, INT, INT, UINT, RECT, LPCWSTR, UINT, ctypes.c_void_p]
ExtTextOutW.restype = BOOL

class TestApp:
    def __init__(self, root):
        self.root = root
        self.is_64bits = sys.maxsize > 2**32
        self.arch = "64-bit" if self.is_64bits else "32-bit"
        self.root.title(f"Hook Test Target ({self.arch}) PID: {os.getpid()}")
        self.root.geometry("400x450")
        
        self.label = tk.Label(root, text=f"PID: {os.getpid()} [{self.arch}]\n1. Run ScreenTranslator\n2. Select this window/PID\n3. Click buttons below to test hooks")
        self.label.pack(pady=10)
        
        if self.is_64bits:
             tk.Label(root, text="WARNING: Running as 64-bit!\nIf testing 32-bit HookAgent, use 32-bit Python.", fg="red").pack()

        
        tk.Button(root, text="Test 1: GDI TextOutW ('Hello GDI')", command=self.test_textout).pack(pady=5)
        tk.Button(root, text="Test 2: GDI ExtTextOutW ('Hello Ext')", command=self.test_exttextout).pack(pady=5)
        tk.Button(root, text="Test 3: GDI ExtTextOutW + GlyphIndex", command=self.test_glyphindex).pack(pady=5)
        tk.Button(root, text="Test 4: Typewriter Effect ('H...e...l...l...o')", command=self.test_typewriter).pack(pady=5)
        tk.Button(root, text="Test 5: MultiByteToWideChar", command=self.test_mb2wc).pack(pady=5)
        
        self.status = tk.Label(root, text="Ready")
        self.status.pack(pady=20)

    def get_hwnd(self):
        return self.root.winfo_id()

    def get_dc(self):
        hwnd = self.get_hwnd()
        return GetDC(hwnd)

    def release_dc(self, hdc):
        hwnd = self.get_hwnd()
        ReleaseDC(hwnd, hdc)

    def test_textout(self):
        hdc = self.get_dc()
        text = "Hello GDI TextOutW"
        TextOutW(hdc, 50, 200, text, len(text))
        self.release_dc(hdc)
        self.status.config(text="Called TextOutW")

    def test_exttextout(self):
        hdc = self.get_dc()
        text = "Hello GDI ExtTextOutW"
        ExtTextOutW(hdc, 50, 230, 0, None, text, len(text), None)
        self.release_dc(hdc)
        self.status.config(text="Called ExtTextOutW")

    def test_glyphindex(self):
        hdc = self.get_dc()
        
        # Correctly use GetGlyphIndicesW to get indices
        text = "Hello GlyphIndex"
        count = len(text)
        
        # GetGlyphIndicesW definition
        GetGlyphIndicesW = gdi32.GetGlyphIndicesW
        GetGlyphIndicesW.argtypes = [HDC, LPCWSTR, INT, ctypes.POINTER(ctypes.c_uint16), UINT]
        GetGlyphIndicesW.restype = UINT
        
        indices = (ctypes.c_uint16 * count)()
        GGI_MARK_NONEXISTING_GLYPHS = 0x0001
        
        # This call should be captured by hookGdiExtras
        GetGlyphIndicesW(hdc, text, count, indices, GGI_MARK_NONEXISTING_GLYPHS)
        
        # Now draw using indices
        ExtTextOutW(hdc, 50, 260, ETO_GLYPH_INDEX, None, ctypes.cast(indices, LPCWSTR), count, None)
        
        self.release_dc(hdc)
        self.status.config(text="Called ExtTextOutW (GlyphIndex)")

    def test_typewriter(self):
        # Test 4: Simulate typewriter effect with growing string (common in VN)
        self.typewriter_full_text = "Hello Typewriter Effect Testing..."
        self.typewriter_index = 0
        self.status.config(text="Running Typewriter...")
        self.type_next_char()

    def type_next_char(self):
        if self.typewriter_index <= len(self.typewriter_full_text):
            current_text = self.typewriter_full_text[:self.typewriter_index]
            hdc = self.get_dc()
            # Draw full string up to current index
            if current_text:
                TextOutW(hdc, 50, 300, current_text, len(current_text))
            self.release_dc(hdc)
            
            self.typewriter_index += 1
            # 50ms delay = 20 chars/sec (matches game speed)
            self.root.after(50, self.type_next_char) 
        else:
            self.status.config(text="Typewriter Done")

    def test_mb2wc(self):
        # Simulate converting a string
        text = "Hello MultiByte"
        src = text.encode('ascii') 
        
        # ctypes definition for MB2WC
        MultiByteToWideChar = kernel32.MultiByteToWideChar
        MultiByteToWideChar.argtypes = [UINT, UINT, ctypes.c_char_p, INT, LPCWSTR, INT]
        MultiByteToWideChar.restype = INT
        
        needed = MultiByteToWideChar(0, 0, src, len(src), None, 0)
        dst = ctypes.create_unicode_buffer(needed)
        MultiByteToWideChar(0, 0, src, len(src), dst, needed)
        
        self.status.config(text=f"Converted: {dst.value}")

if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = TestApp(root)
        root.mainloop()
    except Exception as e:
        print(f"Error: {e}")
        input("Press Enter to exit...")
