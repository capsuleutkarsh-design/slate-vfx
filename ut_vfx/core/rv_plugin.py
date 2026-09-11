"""
The UT VFX menu inside OpenRV.

This runs *inside* RV, not in the main application, so it can only use RV's own
Python. It writes what the supervisor did to ~/.utvfx/rv_feedback.json, which
the software watches.

What goes back is a verdict, the frame it was given on, whatever the supervisor
typed, and - when RV has annotations on that frame - an exported image of the
frame with the drawing on it, so the artist sees what was circled rather than
just being told something is wrong.
"""

import rv
import rv.commands as commands
import rv.rvtypes as rvtypes
import rv.qtutils as qtutils
import os
import json
import time


FEEDBACK_DIR = os.path.join(os.path.expanduser("~"), ".utvfx")
ANNOTATION_DIR = os.path.join(FEEDBACK_DIR, "annotations")


class UTVFXLinkMode(rvtypes.MinorMode):
    def __init__(self):
        rvtypes.MinorMode.__init__(self)
        self.init("UTVFXLink", None, None, [
            ("UT VFX", [
                ("Approve Current Shot", self.approve_shot, None, None),
                ("Retake Current Shot", self.reject_shot, None, None),
                ("_", None, None, None),
                ("Send Note on This Frame...", self.send_note, None, None),
            ])
        ])

    # ------------------------------------------------------------------ media

    def current_media(self):
        """The file being looked at, or "" when nothing is loaded."""
        frame = commands.frame()
        sources = commands.sourcesAtFrame(frame)
        if not sources:
            return ""
        try:
            media_prop = "%s.media.movie" % sources[0]
            return commands.getStringProperty(media_prop, 0, 1)[0]
        except Exception:
            return ""

    def source_frame(self):
        """
        The frame number as the artist knows it.

        A timeline frame is not the frame in the file: reviewing a sequence
        that starts at 1001 shows frame 1 in RV, and telling an artist to look
        at frame 1 sends them to the wrong place.
        """
        frame = commands.frame()
        try:
            info = commands.sourceFrame(frame)
            return int(info)
        except Exception:
            return int(frame)

    # ------------------------------------------------------------ annotations

    def has_annotation(self):
        """Whether anything has been drawn on the current frame."""
        try:
            return bool(commands.findAnnotatedFrames()
                        and commands.frame() in commands.findAnnotatedFrames())
        except Exception:
            return False

    def export_annotated_frame(self, shot_hint=""):
        """
        Save the current frame with its annotation drawn on, and return the path.

        Returns "" when there is nothing drawn or the export is not available -
        a note without a picture is still worth sending.
        """
        if not self.has_annotation():
            return ""

        try:
            if not os.path.exists(ANNOTATION_DIR):
                os.makedirs(ANNOTATION_DIR)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            base = shot_hint or "frame"
            out_path = os.path.join(
                ANNOTATION_DIR, "%s_%s_%d.jpg" % (base, stamp, commands.frame())
            )

            frame = commands.frame()
            commands.exportCurrentFrame(out_path)
            if os.path.exists(out_path):
                return out_path

            # Some builds export through the session writer instead.
            commands.writeAnnotatedFrames([frame], out_path, True)
            return out_path if os.path.exists(out_path) else ""
        except Exception:
            return ""

    # ---------------------------------------------------------------- sending

    def ask_for_note(self):
        """Ask the supervisor what is wrong. Returns None if they cancelled."""
        try:
            from PySide2 import QtWidgets
        except ImportError:
            try:
                from PySide6 import QtWidgets
            except ImportError:
                return ""

        text, ok = QtWidgets.QInputDialog.getMultiLineText(
            qtutils.sessionWindow(), "UT VFX", "Note for this frame:", ""
        )
        if not ok:
            return None
        return text.strip()

    def send(self, status, note=""):
        try:
            media_path = self.current_media()
            if not media_path:
                commands.displayFeedback("No media loaded", 2.0)
                return

            shot_hint = os.path.splitext(os.path.basename(media_path))[0]
            annotation_path = self.export_annotated_frame(shot_hint)

            if not os.path.exists(FEEDBACK_DIR):
                os.makedirs(FEEDBACK_DIR)

            data = {
                "status": status,
                "media_path": media_path,
                "frame": self.source_frame(),
                "note": note or "",
                "annotation_path": annotation_path,
                "reviewer": os.environ.get("USERNAME") or os.environ.get("USER") or "",
                "timestamp": time.time(),
            }

            feedback_file = os.path.join(FEEDBACK_DIR, "rv_feedback.json")
            with open(feedback_file, "w", encoding="utf-8") as f:
                json.dump(data, f)

            message = "UT VFX: %s" % status.upper()
            if annotation_path:
                message += " (with annotation)"
            commands.displayFeedback(message, 2.0)
        except Exception as e:
            commands.displayFeedback("UT VFX Error: %s" % str(e), 3.0)

    # ---------------------------------------------------------------- actions

    def approve_shot(self, event):
        self.send("approved")

    def reject_shot(self, event):
        note = self.ask_for_note()
        if note is None:
            return
        self.send("rejected", note)

    def send_note(self, event):
        """A note without a verdict: the shot's status is left alone."""
        note = self.ask_for_note()
        if note is None:
            return
        if not note:
            commands.displayFeedback("Nothing typed - no note sent", 2.0)
            return
        self.send("note", note)


def createMode():
    return UTVFXLinkMode()
