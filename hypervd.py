import sys
import json
import subprocess
import os
import math
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QPushButton, QGraphicsView, QGraphicsScene, 
                             QGraphicsRectItem, QGraphicsTextItem, QGraphicsItem, 
                             QInputDialog, QMenu, QFileDialog, QColorDialog,
                             QStyle, QGraphicsPixmapItem, QDialog, QFormLayout, 
                             QLineEdit, QLabel, QDialogButtonBox, QMessageBox, 
                             QGraphicsPathItem, QTextEdit)
from PyQt6.QtCore import Qt, QPointF, QTimer, QThread, pyqtSignal, QLineF
from PyQt6.QtGui import (QPen, QBrush, QColor, QPainterPath, QPainter, QPixmap, 
                         QTransform, QPolygonF, QPainterPathStroker, QAction) # QAction goes here

ICON_CHOICES = {
    "Desktop": QStyle.StandardPixmap.SP_DesktopIcon,
    "Server": QStyle.StandardPixmap.SP_DriveHDIcon,
    "Network": QStyle.StandardPixmap.SP_DriveNetIcon,
    "Computer": QStyle.StandardPixmap.SP_ComputerIcon,
    "Warning": QStyle.StandardPixmap.SP_MessageBoxWarning,
    "Database": QStyle.StandardPixmap.SP_DirIcon
}

# --- Background Worker for PowerShell ---
class PSRefreshWorker(QThread):
    data_ready = pyqtSignal(str)
    
    def run(self):
        # FIX: Explicitly iterating adapters to prevent $_ pipeline variable conflicts
        cmd = """
        @(Get-VM | Select-Object Name, 
            @{N='State';E={$_.State.ToString()}}, 
            @{N='Status';E={$_.Status.ToString()}},
            @{N='IPs';E={$_.NetworkAdapters.IPAddresses -join ', '}},
            @{N='MACAddress';E={$_.NetworkAdapters.MacAddress -join ', '}},
            @{N='SwitchName';E={$_.NetworkAdapters.SwitchName -join ', '}},
            @{N='VLAN';E={
                $vlanList = @()
                $adapters = Get-VMNetworkAdapter -VM $_
                foreach ($adapter in $adapters) {
                    $vlanInfo = Get-VMNetworkAdapterVlan -VMNetworkAdapter $adapter
                    if ($vlanInfo.OperationMode -eq 'Trunk') {
                        $vlanList += "Trunk ($($vlanInfo.AllowedVlanIdList))"
                    } elseif ($vlanInfo.AccessVlanId -gt 0) {
                        $vlanList += [string]$vlanInfo.AccessVlanId
                    } else {
                        $vlanList += 'Untagged'
                    }
                }
                if ($vlanList.Count -eq 0) { 'Untagged' } else { $vlanList -join ', ' }
            }},
            @{N='UptimeSec';E={if ($_.Uptime) { [math]::Round($_.Uptime.TotalSeconds) } else { 0 }}},
            CPUUsage, MemoryAssigned
        ) | ConvertTo-Json -Depth 5
        """
        try:
            res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], 
                                 capture_output=True, text=True, creationflags=0x08000000)
            if res.returncode == 0 and res.stdout.strip():
                self.data_ready.emit(res.stdout.strip())
        except Exception as e:
            print(f"PS Worker Error: {e}")

# --- Custom Details Dialogs ---
class RichInfoDialog(QDialog):
    def __init__(self, card, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Details: {card.nickname or card.name}")
        self.resize(500, 400)
        layout = QVBoxLayout(self)
        
        text_area = QTextEdit()
        text_area.setReadOnly(True)
        text_area.setPlainText(json.dumps(card.vm_data, indent=4))
        layout.addWidget(text_area)
        
        # This will now work because QPushButton is in the imports above
        btn = QPushButton("OK")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)

class VMDetailsDialog(QDialog):
    def __init__(self, card, parent=None):
        super().__init__(parent)
        self.card = card
        self.setWindowTitle(f"{'Dummy Edit' if card.is_dummy else 'Edit VM Nickname'} - {card.name}")
        self.setMinimumWidth(350)
        
        layout = QFormLayout(self)
        self.nickname_edit = QLineEdit(self.card.nickname)
        layout.addRow("Nickname:", self.nickname_edit)

        if self.card.is_dummy:
            self.name_edit = QLineEdit(self.card.name)
            self.state_edit = QLineEdit(self.card.state)
            self.ip_edit = QLineEdit(str(self.card.vm_data.get('IPs', 'N/A')))
            self.uptime_edit = QLineEdit(str(self.card.uptime_sec))
            
            layout.addRow("Real Name:", self.name_edit)
            layout.addRow("State:", self.state_edit)
            layout.addRow("IPs:", self.ip_edit)
            layout.addRow("Uptime (Sec):", self.uptime_edit)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.save_data)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def save_data(self):
        self.card.nickname = self.nickname_edit.text().strip()
        self.card.vm_data['Nickname'] = self.card.nickname
        
        if self.card.is_dummy:
            self.card.name = self.name_edit.text().strip()
            self.card.state = self.state_edit.text().strip()
            self.card.uptime_sec = int(self.uptime_edit.text().strip() if self.uptime_edit.text().isdigit() else 0)
            
            self.card.vm_data['Name'] = self.card.name
            self.card.vm_data['State'] = self.card.state
            self.card.vm_data['IPs'] = self.ip_edit.text().strip()
            self.card.vm_data['UptimeSec'] = self.card.uptime_sec

        self.card.update_colors()
        self.card.update_text_display()
        if self.card.scene():
            self.card.scene().unsaved_changes = True
        self.accept()

# --- Custom View for Pan & Zoom ---
class DashboardView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def wheelEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            zoom_in_factor = 1.15
            zoom_out_factor = 1.0 / zoom_in_factor
            if event.angleDelta().y() > 0:
                self.scale(zoom_in_factor, zoom_in_factor)
            else:
                self.scale(zoom_out_factor, zoom_out_factor)
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Control:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Control:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        super().keyReleaseEvent(event)

# --- Fully Editable Connection Class ---
class ConnectionLine(QGraphicsPathItem):
    def __init__(self, source, target, color="#27ae60", line_style=Qt.PenStyle.SolidLine, arrow_style="None"):
        super().__init__()
        self.source = source
        self.target = target
        self.line_color = QColor(color)
        self.line_style = line_style
        self.arrow_style = arrow_style
        
        self.setZValue(0) 
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.update_appearance()
        self.update_position()

    def update_appearance(self):
        pen = QPen(self.line_color, 2, self.line_style)
        self.setPen(pen)
        self.update()
        if self.scene():
            self.scene().unsaved_changes = True

    def _get_edge_point(self, top_left, width, height, target_pt):
        center = top_left + QPointF(width/2, height/2)
        line = QLineF(center, target_pt)
        
        top = QLineF(top_left, top_left + QPointF(width, 0))
        bottom = QLineF(top_left + QPointF(0, height), top_left + QPointF(width, height))
        left = QLineF(top_left, top_left + QPointF(0, height))
        right = QLineF(top_left + QPointF(width, 0), top_left + QPointF(width, height))
        
        for edge in [top, bottom, left, right]:
            intersection_type, pt = line.intersects(edge)
            if intersection_type == QLineF.IntersectionType.BoundedIntersection:
                return pt
        return center

    def update_position(self):
        if not self.source.scene() or not self.target.scene(): return
            
        # Dynamically query dimensions so routing adapts to filtered card sizes
        src_rect = self.source.boundingRect()
        tgt_rect = self.target.boundingRect()
            
        src_center = self.source.scenePos() + src_rect.center()
        tgt_center = self.target.scenePos() + tgt_rect.center()
        
        dx = tgt_center.x() - src_center.x()
        dy = tgt_center.y() - src_center.y()
        
        if abs(dx) > abs(dy):
            c1_base = QPointF(src_center.x() + dx * 0.5, src_center.y())
            c2_base = QPointF(tgt_center.x() - dx * 0.5, tgt_center.y())
        else:
            c1_base = QPointF(src_center.x(), src_center.y() + dy * 0.5)
            c2_base = QPointF(tgt_center.x(), tgt_center.y() - dy * 0.5)

        s_tl = self.source.scenePos() + QPointF(-4, -4)
        t_tl = self.target.scenePos() + QPointF(-4, -4)
        
        # Calculate exactly where the line exits the dynamic box
        self.start_pt = self._get_edge_point(s_tl, src_rect.width() + 8, src_rect.height() + 8, c1_base)
        self.end_pt = self._get_edge_point(t_tl, tgt_rect.width() + 8, tgt_rect.height() + 8, c2_base)
        
        dx_actual = self.end_pt.x() - self.start_pt.x()
        dy_actual = self.end_pt.y() - self.start_pt.y()
        
        if abs(dx) > abs(dy):
            self.c1 = QPointF(self.start_pt.x() + dx_actual * 0.5, self.start_pt.y())
            self.c2 = QPointF(self.end_pt.x() - dx_actual * 0.5, self.end_pt.y())
        else:
            self.c1 = QPointF(self.start_pt.x(), self.start_pt.y() + dy_actual * 0.5)
            self.c2 = QPointF(self.end_pt.x(), self.end_pt.y() - dy_actual * 0.5)
        
        path = QPainterPath()
        path.moveTo(self.start_pt)
        path.cubicTo(self.c1, self.c2, self.end_pt)
        self.setPath(path)

    def boundingRect(self):
        extra = 50.0
        return self.path().boundingRect().adjusted(-extra, -extra, extra, extra)

    def shape(self):
        path_stroker = QPainterPathStroker()
        path_stroker.setWidth(15)
        return path_stroker.createStroke(self.path())

    def paint(self, painter, option, widget=None):
        pen = self.pen()
        if self.isSelected():
            pen.setWidth(4)
        painter.setPen(pen)
        painter.drawPath(self.path())

        if self.arrow_style != "None":
            painter.setBrush(QBrush(self.line_color))
            painter.setPen(Qt.PenStyle.NoPen)
            
            if self.arrow_style in ["Start", "Both"]:
                angle = math.atan2(self.start_pt.y() - self.c1.y(), self.start_pt.x() - self.c1.x())
                self._draw_arrow(painter, self.start_pt, angle)
                
            if self.arrow_style in ["End", "Both"]:
                angle = math.atan2(self.end_pt.y() - self.c2.y(), self.end_pt.x() - self.c2.x())
                self._draw_arrow(painter, self.end_pt, angle)

    def _draw_arrow(self, painter, point, angle):
        arrow_size = 16
        fin_angle1 = angle + math.pi - math.pi/7
        fin_angle2 = angle + math.pi + math.pi/7
        p1 = point + QPointF(math.cos(fin_angle1) * arrow_size, math.sin(fin_angle1) * arrow_size)
        p2 = point + QPointF(math.cos(fin_angle2) * arrow_size, math.sin(fin_angle2) * arrow_size)
        painter.drawPolygon(QPolygonF([point, p1, p2]))

    def contextMenuEvent(self, event):
        menu = QMenu()
        color_act = menu.addAction("Change Color")
        
        style_menu = menu.addMenu("Line Style")
        solid_act = style_menu.addAction("Solid")
        dashed_act = style_menu.addAction("Dashed")
        
        arrow_menu = menu.addMenu("Arrows")
        arr_none = arrow_menu.addAction("None")
        arr_start = arrow_menu.addAction("Start Arrow")
        arr_end = arrow_menu.addAction("End Arrow")
        arr_both = arrow_menu.addAction("Both Arrows")
        
        menu.addSeparator()
        del_act = menu.addAction("Delete Connection")
        
        act = menu.exec(event.screenPos())
        if act == color_act:
            color = QColorDialog.getColor(self.line_color)
            if color.isValid():
                self.line_color = color
                self.update_appearance()
        elif act == solid_act:
            self.line_style = Qt.PenStyle.SolidLine; self.update_appearance()
        elif act == dashed_act:
            self.line_style = Qt.PenStyle.DashLine; self.update_appearance()
        elif act == arr_none:
            self.arrow_style = "None"; self.update(); self.scene().unsaved_changes = True
        elif act == arr_start:
            self.arrow_style = "Start"; self.update(); self.scene().unsaved_changes = True
        elif act == arr_end:
            self.arrow_style = "End"; self.update(); self.scene().unsaved_changes = True
        elif act == arr_both:
            self.arrow_style = "Both"; self.update(); self.scene().unsaved_changes = True
        elif act == del_act:
            self.scene().remove_connection(self)

# --- UI Components ---
class ResizeHandle(QGraphicsRectItem):
    def __init__(self, parent):
        super().__init__(0, 0, 15, 15, parent)
        self.setBrush(QBrush(QColor("#adb5bd")))
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.resizing = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.resizing = True
            event.accept()

    def mouseMoveEvent(self, event):
        if self.resizing:
            parent = self.parentItem()
            pos = parent.mapFromScene(event.scenePos())
            new_w = max(150, pos.x())
            new_h = max(100, pos.y())
            parent.setRect(0, 0, new_w, new_h)
            self.setPos(new_w - 15, new_h - 15)
            if self.scene(): self.scene().unsaved_changes = True
            event.accept()

    def mouseReleaseEvent(self, event):
        self.resizing = False
        event.accept()

class ImageResizeHandle(QGraphicsRectItem):
    def __init__(self, parent):
        super().__init__(0, 0, 15, 15, parent)
        self.setBrush(QBrush(QColor(0, 0, 0, 120)))
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.resizing = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.resizing = True
            event.accept()

    def mouseMoveEvent(self, event):
        if self.resizing:
            parent = self.parentItem()
            pos = parent.mapFromScene(event.scenePos())
            rect = parent.boundingRect()
            if rect.width() > 0:
                scale = max(0.1, pos.x() / rect.width())
                parent.setScale(scale)
            if self.scene(): self.scene().unsaved_changes = True
            event.accept()

    def mouseReleaseEvent(self, event):
        self.resizing = False
        event.accept()

class ResizableImage(QGraphicsPixmapItem):
    def __init__(self, filepath, x, y, scale=1.0):
        self.filepath = filepath
        super().__init__(QPixmap(filepath))
        self.setPos(x, y)
        self.setScale(scale)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        
        rect = self.boundingRect()
        self.handle = ImageResizeHandle(self)
        self.handle.setPos(rect.width() - 15, rect.height() - 15)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if self.scene(): self.scene().unsaved_changes = True
        return super().itemChange(change, value)

class CommentBox(QGraphicsTextItem):
    def __init__(self, text, x, y):
        super().__init__(text)
        self.setPos(x, y)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        
        font = self.font()
        font.setPointSize(12)
        self.setFont(font)
        self.setDefaultTextColor(QColor("#333333"))

    def mouseDoubleClickEvent(self, event):
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus()
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event):
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        if self.scene(): self.scene().unsaved_changes = True
        super().focusOutEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if self.scene(): self.scene().unsaved_changes = True
        return super().itemChange(change, value)

class InfoButton(QGraphicsRectItem):
    def __init__(self, parent):
        super().__init__(0, 0, 20, 20, parent)
        self.setBrush(QBrush(QColor("#e0e0e0")))
        self.setPen(QPen(QColor("#999999")))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        text = QGraphicsTextItem("i", self)
        text.setDefaultTextColor(QColor("#333333"))
        text.setPos(4, 0)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.parentItem().show_info()
            event.accept()

class ZoneBox(QGraphicsRectItem):
    def __init__(self, name, x, y, width=250, height=400):
        super().__init__(0, 0, width, height)
        self.setPos(x, y)
        self.name = name
        self.bg_color = "#f8f9fa"
        self.setZValue(-10)
        
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        
        self.setBrush(QBrush(QColor(self.bg_color)))
        self.setPen(QPen(QColor("#ced4da"), 2, Qt.PenStyle.DashLine))
        
        self.title = QGraphicsTextItem(f"ZONE: {name}", self)
        self.title.setDefaultTextColor(QColor("#495057"))
        self.title.setPos(5, 5)

        self.handle = ResizeHandle(self)
        self.handle.setPos(width - 15, height - 15)

    def prompt_rename(self):
        new_name, ok = QInputDialog.getText(None, "Rename Zone", "Enter new zone name:", text=self.name)
        if ok and new_name.strip():
            self.name = new_name.strip()
            self.title.setPlainText(f"ZONE: {self.name}")
            if self.scene(): self.scene().unsaved_changes = True

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if self.scene(): self.scene().unsaved_changes = True
        return super().itemChange(change, value)

class VMCard(QGraphicsRectItem):
    def __init__(self, vm_data, x, y, display_options=None):
        super().__init__(0, 0, 240, 100) 
        self.setPos(x, y)
        self.vm_data = vm_data
        
        # 1. DEFINE ATTRIBUTES FIRST
        self.display_options = display_options or vm_data.get('display_options', {
            'State': True, 'IP': True, 'Uptime': True, 
            'MAC': False, 'Switch': True, 'VLAN': False, 'CPU': False, 'RAM': False
        })
        self.vm_data['display_options'] = self.display_options
        
        self.name = vm_data.get('Name', 'Unknown')
        self.nickname = vm_data.get('Nickname', '')
        self.state = str(vm_data.get('State', 'Unknown'))
        self.uptime_sec = int(vm_data.get('UptimeSec', 0))
        self.icon_name = vm_data.get('icon', 'Desktop')
        self.is_dummy = vm_data.get('is_dummy', False)
        
        # 2. THEN SET GRAPHICS/UI
        self.setZValue(10)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        
        self.setBrush(QBrush(QColor("#ffffff")))
        self.update_colors()
        
        self.icon_item = QGraphicsPixmapItem(self)
        self.icon_item.setPos(5, 10)
        self.set_icon(self.icon_name)
        
        self.text = QGraphicsTextItem(self)
        self.text.setTextWidth(200) # Enable wrapping
        self.text.setPos(35, 5)
        
        self.info_btn = InfoButton(self)
        
        # 3. FINALLY, CALL DISPLAY
        self.update_text_display()

    def update_text_display(self):
        m, s = divmod(self.uptime_sec, 60)
        h, m = divmod(m, 60)
        d, h = divmod(h, 24)
        
        uptime_str = f"{d}d {h:02d}h {m:02d}m {s:02d}s" if self.uptime_sec > 0 else "Offline"
        if self.uptime_sec > 0 and self.state == 'Paused':
            uptime_str = f"Paused ({uptime_str})"

        title_display = f"<b>{self.nickname}</b> <i>({self.name})</i>" if self.nickname else f"<b>{self.name}</b>"
        html_lines = [title_display]

        # Build lines... (keep your existing build logic here)
        if self.display_options.get('State', True): html_lines.append(f"State: {self.state}")
        if self.display_options.get('IP', True): html_lines.append(f"IP: {self.vm_data.get('IPs', 'N/A')}")
        if self.display_options.get('Uptime', True): html_lines.append(f"Uptime: {uptime_str}")
        if self.display_options.get('MAC', False): html_lines.append(f"MAC: {self.vm_data.get('MACAddress', 'N/A')}")
        if self.display_options.get('Switch', False): html_lines.append(f"Switch: {self.vm_data.get('SwitchName', 'N/A')}")
        if self.display_options.get('VLAN', False): html_lines.append(f"VLAN: {self.vm_data.get('VLAN', 'N/A')}")
        if self.display_options.get('CPU', False): html_lines.append(f"CPU: {self.vm_data.get('CPUUsage', '0')}%")
        if self.display_options.get('RAM', False):
            ram = self.vm_data.get('MemoryAssigned', '0')
            try: html_lines.append(f"RAM: {round(int(ram) / (1024**3), 1)} GB")
            except: html_lines.append(f"RAM: {ram}")

        # UPDATE TEXT ONLY
        self.text.setHtml("<br>".join(html_lines))
        
        # DO NOT call setRect here during the tick!
        # Only set it if we detect the card is brand new or filters changed
        if not hasattr(self, '_initialized'):
            new_height = max(100, 35 + (len(html_lines) * 18))
            self.setRect(0, 0, 240, new_height)
            self.info_btn.setPos(215, 5)
            self._initialized = True

    def paint(self, painter, option, widget=None):
        painter.setBrush(self.brush())
        pen = self.pen()
        
        if self.is_dummy:
            pen.setStyle(Qt.PenStyle.DashLine)
            if 'border_color' in self.vm_data:
                pen.setColor(QColor(self.vm_data['border_color']))
            
        painter.setPen(pen)
        painter.drawRect(self.boundingRect())

        if not self.is_dummy:
            painter.setBrush(QBrush(QColor("#3498db")))
            painter.setPen(Qt.PenStyle.NoPen)
            poly = QPolygonF([QPointF(0, 0), QPointF(15, 0), QPointF(0, 15)])
            painter.drawPolygon(poly)

    def set_icon(self, icon_name):
        self.icon_name = icon_name
        self.vm_data['icon'] = icon_name 
        icon_enum = ICON_CHOICES.get(icon_name, QStyle.StandardPixmap.SP_DesktopIcon)
        icon = QApplication.style().standardIcon(icon_enum)
        self.icon_item.setPixmap(icon.pixmap(24, 24))
        if self.scene(): self.scene().unsaved_changes = True

    def update_colors(self):
        if self.state == 'Running':
            pen_color = "#2ecc71"
        elif self.state == 'Paused':
            pen_color = "#f1c40f"
        elif self.state == 'Decommissioned':
            pen_color = "#95a5a6"
        else:
            pen_color = "#e74c3c"
            
        pen = QPen(QColor(pen_color), 2)
        if self.is_dummy:
            pen.setStyle(Qt.PenStyle.DashLine)
            if 'border_color' in self.vm_data:
                pen.setColor(QColor(self.vm_data['border_color']))
                
        self.setPen(pen)
        self.update() 

    def update_text_display(self):
        m, s = divmod(self.uptime_sec, 60)
        h, m = divmod(m, 60)
        d, h = divmod(h, 24)
        
        if self.uptime_sec > 0:
            uptime_str = f"{d}d {h:02d}h {m:02d}m {s:02d}s"
            if self.state == 'Paused':
                uptime_str = f"Paused ({uptime_str})"
        else:
            uptime_str = "Offline"

        title_display = f"<b>{self.nickname}</b> <i>({self.name})</i>" if self.nickname else f"<b>{self.name}</b>"
        html_lines = [title_display]

        # Dynamically build text based on selected global options
        if self.display_options.get('State', True):
            html_lines.append(f"State: {self.state}")
        if self.display_options.get('IP', True):
            ips = self.vm_data.get('IPs', 'N/A') or 'N/A'
            html_lines.append(f"IP: {ips}")
        if self.display_options.get('Uptime', True):
            html_lines.append(f"Uptime: {uptime_str}")
        if self.display_options.get('MAC', False):
            mac = self.vm_data.get('MACAddress', 'N/A') or 'N/A'
            html_lines.append(f"MAC: {mac}")
        if self.display_options.get('Switch', False):
            switch = self.vm_data.get('SwitchName', 'N/A') or 'N/A'
            html_lines.append(f"Switch: {switch}")
        if self.display_options.get('CPU', False):
            cpu = self.vm_data.get('CPUUsage', '0')
            html_lines.append(f"CPU: {cpu}%")
        if self.display_options.get('VLAN', False):
            vlan_raw = str(self.vm_data.get('VLAN', 'N/A'))
            
            if vlan_raw in ['N/A', '', 'None']:
                vlan_str = 'N/A'
            else:
                # Split the string in case of multiple network adapters
                vlans = [v.strip() for v in vlan_raw.split(',')]
                
                # Convert any '0' to 'Untagged'
                vlans = ['Untagged' if v == '0' else v for v in vlans]
                
                # If ALL adapters are untagged, just show "Untagged" once to save space
                if all(v == 'Untagged' for v in vlans):
                    vlan_str = 'Untagged'
                else:
                    vlan_str = ', '.join(vlans)
                    
            html_lines.append(f"VLAN: {vlan_str}")
        if self.display_options.get('RAM', False):
            ram = self.vm_data.get('MemoryAssigned', '0')
            try:
                ram_gb = round(int(ram) / (1024**3), 1)
                html_lines.append(f"RAM: {ram_gb} GB")
            except:
                html_lines.append(f"RAM: {ram}")

        self.text.setHtml("<br>".join(html_lines))
        
        # Dynamically resize the card depending on how much text is shown
        num_lines = len(html_lines)
        new_height = max(100, 35 + (num_lines * 16))
        
        # FIX: Use self.rect() instead of self.boundingRect()
        current_rect = self.rect()
        self.setRect(0, 0, current_rect.width(), new_height)
        
        # Lock info button to the top right corner
        self.info_btn.setPos(current_rect.width() - 25, 5)

        # Tell scene to adapt connection arrows to the new shape sizes
        if self.scene():
            self.scene().update_connection_positions()

    def show_info(self):
        dialog = RichInfoDialog(self)
        dialog.exec()

    def show_edit(self):
        dialog = VMDetailsDialog(self)
        dialog.exec()

    def contextMenuEvent(self, event):
        menu = QMenu()
        info_action = menu.addAction("Show Full JSON")
        edit_action = menu.addAction("Edit Nickname / Data")
        
        icon_menu = menu.addMenu("Change Icon...")
        for name in ICON_CHOICES.keys():
            action = icon_menu.addAction(name)
            action.triggered.connect(lambda checked, n=name: self.set_icon(n))

        if self.is_dummy:
            border_action = menu.addAction("Change Border Color")

        menu.addSeparator()
        connect_action = menu.addAction("Start Manual Connection")
        delete_action = menu.addAction("Delete VM")

        action = menu.exec(event.screenPos())
        if action == info_action:
            self.show_info()
        elif action == edit_action:
            self.show_edit()
        elif self.is_dummy and action == border_action:
            color = QColorDialog.getColor(self.pen().color())
            if color.isValid():
                self.vm_data['border_color'] = color.name()
                self.update_colors()
                if self.scene(): self.scene().unsaved_changes = True
        elif action == connect_action:
            self.scene().start_manual_link(self)
        elif action == delete_action:
            current_scene = self.scene()
            if current_scene:
                conns_to_remove = [c for c in current_scene.connections if c.source == self or c.target == self]
                for c in conns_to_remove:
                    current_scene.remove_connection(c)
                current_scene.removeItem(self)

    def mousePressEvent(self, event):
        if self.scene() and self.scene().linking_source:
            if event.button() == Qt.MouseButton.LeftButton:
                self.scene().complete_manual_link(self)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.scene() and not self.scene().linking_source:
            new_card = VMCard(self.vm_data.copy(), self.x() + 20, self.y() + 20, self.display_options)
            self.scene().addItem(new_card)
            
            conn = ConnectionLine(self, new_card, color="#3498db")
            self.scene().addItem(conn)
            self.scene().connections.append(conn)
            self.scene().unsaved_changes = True
            
        event.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if self.scene():
                self.scene().update_connection_positions()
                self.scene().unsaved_changes = True
        return super().itemChange(change, value)


# --- Main Dashboard Engine ---
class DashboardScene(QGraphicsScene):
    def __init__(self, display_options=None):
        super().__init__(0, 0, 4000, 3000)
        self.setBackgroundBrush(QBrush(QColor("#e9ecef")))
        self.connections = [] 
        self.linking_source = None
        self.unsaved_changes = False 
        self.lines = []
        self.display_options = display_options or {}

    def start_manual_link(self, source_card):
        self.linking_source = source_card
        QApplication.setOverrideCursor(Qt.CursorShape.CrossCursor)

    def complete_manual_link(self, target_card):
        if self.linking_source and self.linking_source != target_card:
            conn = ConnectionLine(self.linking_source, target_card, color="#27ae60")
            self.addItem(conn)
            self.connections.append(conn)
            self.unsaved_changes = True
        
        self.linking_source = None
        QApplication.restoreOverrideCursor()

    def remove_connection(self, conn):
        if conn in self.connections:
            self.connections.remove(conn)
        self.removeItem(conn)
        self.unsaved_changes = True

    def update_connection_positions(self):
        for conn in self.connections:
            conn.update_position()

    def show_background_menu(self, scene_pos, screen_pos, item):
        menu = QMenu()
        dummy_act = menu.addAction("Add Dummy Object")
        comment_act = menu.addAction("Add Comment")
        img_act = menu.addAction("Add Image")
        
        if isinstance(item, ZoneBox):
            menu.addSeparator()
            color_act = menu.addAction("Change Zone Color")
            ren_act = menu.addAction("Rename Zone")
        else:
            color_act = ren_act = None

        action = menu.exec(screen_pos)

        if action == dummy_act:
            dummy_data = {
                'Name': f'Dummy Node {len(self.items())}', 'State': 'Offline', 
                'UptimeSec': 0, 'IPs': '192.168.x.x', 'icon': 'Computer', 'is_dummy': True
            }
            self.addItem(VMCard(dummy_data, scene_pos.x(), scene_pos.y(), self.display_options))
            self.unsaved_changes = True
        elif action == comment_act:
            self.addItem(CommentBox("Double click to edit...", scene_pos.x(), scene_pos.y()))
            self.unsaved_changes = True
        elif action == img_act:
            path, _ = QFileDialog.getOpenFileName(None, "Select Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
            if path:
                self.addItem(ResizableImage(path, scene_pos.x(), scene_pos.y()))
                self.unsaved_changes = True
        elif action == color_act and item:
            color = QColorDialog.getColor(QColor(item.bg_color))
            if color.isValid():
                item.bg_color = color.name()
                item.setBrush(QBrush(QColor(item.bg_color)))
                self.unsaved_changes = True
        elif action == ren_act and item:
            item.prompt_rename()

    def contextMenuEvent(self, event):
        item = self.itemAt(event.scenePos(), QTransform())
        if item is None or isinstance(item, ZoneBox):
            self.show_background_menu(event.scenePos(), event.screenPos(), item)
        else:
            super().contextMenuEvent(event)
            
    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.scenePos(), QTransform())
        if item is None or isinstance(item, ZoneBox):
            self.show_background_menu(event.scenePos(), event.screenPos(), item)
        else:
            super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if self.linking_source:
            item = self.itemAt(event.scenePos(), QTransform())
            if item is None or isinstance(item, ZoneBox):
                self.linking_source = None
                QApplication.restoreOverrideCursor()
        super().mousePressEvent(event)

    def update_lines(self):
        self.unsaved_changes = True
        if hasattr(self, 'lines'):
            for line in self.lines:
                if line.scene() == self:
                    self.removeItem(line)
            self.lines.clear()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            for item in self.selectedItems():
                if isinstance(item, ConnectionLine):
                    self.remove_connection(item)
                else:
                    conns_to_remove = [c for c in self.connections if hasattr(c, 'source') and (c.source == item or c.target == item)]
                    for c in conns_to_remove:
                        self.remove_connection(c)
                    self.removeItem(item)
                    self.unsaved_changes = True
                    
        elif event.key() == Qt.Key.Key_Escape and self.linking_source:
            self.linking_source = None
            QApplication.restoreOverrideCursor()
        super().keyPressEvent(event)

# --- QMainWindow App ---
class HyperVDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hyper-V Visual Dashboard")
        self.resize(1400, 900)
        
        # Set up global view filters
        self.display_options = {
            'State': True, 'IP': True, 'Uptime': True, 
            'MAC': False, 'Switch': True, 'VLAN': False, 'CPU': False, 'RAM': False
        }
        
        # Central Canvas Setup
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.scene = DashboardScene(self.display_options)
        self.view = DashboardView(self.scene)
        layout.addWidget(self.view)
        
        self.scene.addItem(ZoneBox("Unselected", 20, 20))
        
        # Build the Native Top Menu Bar
        self.create_menu_bar()
        
        # Background Processes
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick_uptimes)
        self.tick_timer.start(1000)

        self.ps_worker = None

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.trigger_ps_refresh)
        self.poll_timer.start(10000)

        self.trigger_ps_refresh()

    def create_menu_bar(self):
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("File")
        save_act = QAction("Save Canvas", self)
        save_act.triggered.connect(self.save_layout)
        file_menu.addAction(save_act)

        load_act = QAction("Load Canvas", self)
        load_act.triggered.connect(self.load_layout)
        file_menu.addAction(load_act)

        # Actions Menu
        action_menu = menubar.addMenu("Actions")
        refresh_act = QAction("Manual VM Refresh", self)
        refresh_act.triggered.connect(self.trigger_ps_refresh)
        action_menu.addAction(refresh_act)

        add_zone_act = QAction("Add Zone Background", self)
        add_zone_act.triggered.connect(self.add_new_zone)
        action_menu.addAction(add_zone_act)

        # View Filters Menu
        view_menu = menubar.addMenu("View Filters")
        for key in self.display_options.keys():
            act = QAction(key, self, checkable=True)
            act.setChecked(self.display_options[key])
            # Bind the action to toggle and re-render
            act.triggered.connect(lambda checked, k=key: self.toggle_filter(k, checked))
            view_menu.addAction(act)

    def toggle_filter(self, key, checked):
        self.display_options[key] = checked
        # Tell all cards to rebuild their text dynamically
        for item in self.scene.items():
            if isinstance(item, VMCard):
                item.display_options = self.display_options
                item.update_text_display()

    def add_new_zone(self):
        self.scene.addItem(ZoneBox(f"Zone {len([i for i in self.scene.items() if isinstance(i, ZoneBox)])+1}", 300, 50))
        self.scene.unsaved_changes = True

    def tick_uptimes(self):
        for item in self.scene.items():
            if isinstance(item, VMCard) and item.state == 'Running':
                old_uptime = item.uptime_sec
                item.uptime_sec += 1
                # Only update if the uptime actually changes the string (e.g. every second)
                # But you could even make this every 5 seconds to reduce UI thrash
                item.update_text_display()

    def trigger_ps_refresh(self):
        if self.ps_worker and self.ps_worker.isRunning():
            return

        self.ps_worker = PSRefreshWorker()
        self.ps_worker.data_ready.connect(self.process_ps_data)
        self.ps_worker.start()

    def process_ps_data(self, json_string):
        try:
            vms = json.loads(json_string)
        except json.JSONDecodeError:
            return 
            
        if not isinstance(vms, list): vms = [vms]
        live_names = [v['Name'] for v in vms]
        existing_cards = [i for i in self.scene.items() if isinstance(i, VMCard)]

        for card in existing_cards:
            if not card.is_dummy and card.name not in live_names and card.state != 'Decommissioned':
                card.state = 'Decommissioned'
                card.uptime_sec = 0
                card.update_colors()
                card.update_text_display()
                self.scene.unsaved_changes = True

        added_count = 0
        for vm in vms:
            existing = [c for c in existing_cards if c.name == vm['Name'] and not c.is_dummy]
            if not existing:
                unsel_zone = next((z for z in self.scene.items() if isinstance(z, ZoneBox) and z.name == "Unselected"), None)
                start_x = unsel_zone.x() + 20 if unsel_zone else 40
                start_y = unsel_zone.y() + 50 if unsel_zone else 70
                y_offset = start_y + ((len(existing_cards) + added_count) * 110) 
                
                self.scene.addItem(VMCard(vm, start_x, y_offset, self.display_options))
                added_count += 1
                self.scene.unsaved_changes = True
            else:
                for card in existing:
                    card.vm_data.update(vm)
                    card.state = str(vm.get('State', 'Unknown'))
                    card.uptime_sec = int(vm.get('UptimeSec', 0))
                    card.update_colors()
                    card.update_text_display()

    def save_layout(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Canvas", "view.json", "JSON Files (*.json);;All Files (*)")
        if not file_path: return False

        data = {'zones': [], 'vms': [], 'connections': [], 'comments': [], 'images': [], 'filters': self.display_options}
        vm_to_id = {}
        for idx, item in enumerate([i for i in self.scene.items() if isinstance(i, VMCard)]):
            vm_to_id[item] = idx
            # FIX: Force absolute global coordinates with scenePos()
            data['vms'].append({'id': idx, 'data': item.vm_data, 'x': item.scenePos().x(), 'y': item.scenePos().y()})

        for conn in self.scene.connections:
            if conn.source in vm_to_id and conn.target in vm_to_id:
                style_str = "Dashed" if conn.line_style == Qt.PenStyle.DashLine else "Solid"
                data['connections'].append({
                    'source': vm_to_id[conn.source],
                    'target': vm_to_id[conn.target],
                    'color': conn.line_color.name(),
                    'style': style_str,
                    'arrows': conn.arrow_style
                })

        for item in self.scene.items():
            if isinstance(item, ZoneBox):
                data['zones'].append({
                    'name': item.name, 'x': item.scenePos().x(), 'y': item.scenePos().y(), 
                    'width': item.rect().width(), 'height': item.rect().height(),
                    'bg_color': item.bg_color
                })
            elif isinstance(item, CommentBox):
                data['comments'].append({'text': item.toHtml(), 'x': item.scenePos().x(), 'y': item.scenePos().y()})
            elif isinstance(item, ResizableImage):
                data['images'].append({'path': item.filepath, 'x': item.scenePos().x(), 'y': item.scenePos().y(), 'scale': item.scale()})
                
        with open(file_path, 'w') as f:
            json.dump(data, f)
            
        self.scene.unsaved_changes = False
        return True

    def load_layout(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Load Canvas", "", "JSON Files (*.json);;All Files (*)")
        if not file_path or not os.path.exists(file_path): return
            
        with open(file_path, 'r') as f: data = json.load(f)
            
        self.scene.clear()
        self.scene.connections.clear()
        
        # Load and set global filters
        if 'filters' in data:
            self.display_options.update(data['filters'])
        
        for z in data.get('zones', []):
            new_zone = ZoneBox(z['name'], z['x'], z['y'], z.get('width', 250), z.get('height', 400))
            new_zone.bg_color = z.get('bg_color', '#f8f9fa')
            new_zone.setBrush(QBrush(QColor(new_zone.bg_color)))
            self.scene.addItem(new_zone)
            
        id_to_vm = {}
        for v in data.get('vms', []):
            new_vm = VMCard(v['data'], v['x'], v['y'], self.display_options)
            self.scene.addItem(new_vm)
            id_to_vm[v['id']] = new_vm
            
        for c_data in data.get('connections', []):
            src = id_to_vm.get(c_data.get('source'))
            tgt = id_to_vm.get(c_data.get('target'))
            if src and tgt:
                style = Qt.PenStyle.DashLine if c_data.get('style') == "Dashed" else Qt.PenStyle.SolidLine
                conn = ConnectionLine(src, tgt, c_data.get('color', '#27ae60'), style, c_data.get('arrows', 'None'))
                self.scene.addItem(conn)
                self.scene.connections.append(conn)

        for c in data.get('comments', []):
            comment = CommentBox("", c['x'], c['y'])
            comment.setHtml(c['text'])
            self.scene.addItem(comment)

        for i in data.get('images', []):
            if os.path.exists(i['path']):
                self.scene.addItem(ResizableImage(i['path'], i['x'], i['y'], i['scale']))
                
        self.scene.unsaved_changes = False

    def closeEvent(self, event):
        if self.scene.unsaved_changes:
            reply = QMessageBox.question(
                self, 'Unsaved Changes',
                "You have unsaved changes. Do you want to save your canvas before exiting?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save
            )

            if reply == QMessageBox.StandardButton.Save:
                if self.save_layout():
                    event.accept()
                else:
                    event.ignore() 
            elif reply == QMessageBox.StandardButton.Discard:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = HyperVDashboard()
    window.show()
    sys.exit(app.exec())
