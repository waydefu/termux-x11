#!/usr/bin/env python3
# DND-PROBE-01 drag source: one GTK3 window; dragging from it offers <path> as text/uri-list (what file managers send).
#   dnd_source.py <path>
import sys, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
path = sys.argv[1]
uri = GLib.filename_to_uri(path, None)
w = Gtk.Window(title="dnd-source"); w.set_default_size(400, 400)
b = Gtk.Button(label="DRAG ME"); w.add(b)
b.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [], Gdk.DragAction.COPY)
b.drag_source_add_uri_targets()
def get(widget, ctx, data, info, t):
    data.set_uris([uri]); print("DND_SOURCE_SENT", uri, flush=True)
b.connect("drag-data-get", get)
b.connect("drag-end", lambda *a: print("DND_SOURCE_END", flush=True))
w.connect("destroy", Gtk.main_quit); w.show_all(); print("DND_SOURCE_READY", uri, flush=True); Gtk.main()
