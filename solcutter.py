import sys
import os
from datetime import datetime

# ==========================================
# [PyQt6 라이브러리]
# ==========================================
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QFileDialog, 
                             QSlider, QStyle, QMessageBox, QProgressBar, QFrame)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtCore import Qt, QUrl, QRect, QPoint, QSize, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QPainter, QPen, QColor, QMouseEvent, QFontDatabase, QFont, QIcon

# MoviePy 호환성 처리
try:
    from moviepy.editor import VideoFileClip
except ImportError:
    # MoviePy 2.0 이상 대응
    try:
        from moviepy import VideoFileClip
    except ImportError:
        # 린터 오류 방지용 더미 (실제 실행시엔 위에서 잡힘)
        VideoFileClip = None 
    
from proglog import ProgressBarLogger

# ==========================================
# 1. 커스텀 로거
# ==========================================
class SolCutterLogger(ProgressBarLogger):
    def __init__(self, update_callback):
        super().__init__()
        self.update_callback = update_callback

    def bars_callback(self, bar, attr, value, old_value=None):
        super().bars_callback(bar, attr, value, old_value)
        if bar == 't' and attr == 'index':
            if bar in self.bars:
                total = self.bars[bar]['total']
                if total > 0:
                    percentage = int((value / total) * 100)
                    self.update_callback(percentage)

# ==========================================
# 2. 작업 처리 스레드
# ==========================================
class ExportThread(QThread):
    status_msg = pyqtSignal(str)
    progress_val = pyqtSignal(int)
    finished_signal = pyqtSignal()

    def __init__(self, file_path, output_path, start_t, end_t, crop_rect, mode='video'):
        super().__init__()
        self.file_path = file_path
        self.output_path = output_path
        self.start_t = start_t
        self.end_t = end_t
        self.crop_rect = crop_rect 
        self.mode = mode 

    def run(self):
        try:
            self.status_msg.emit("데이터 준비 중...")
            if VideoFileClip is None:
                raise ImportError("MoviePy 라이브러리가 설치되지 않았습니다.")
                
            clip = VideoFileClip(self.file_path)

            end = self.end_t if self.end_t > 0 else clip.duration
            end = min(end, clip.duration)
            
            subclip = clip.subclip(self.start_t, end)
            my_logger = SolCutterLogger(self.progress_val.emit)

            if self.mode == 'audio':
                self.status_msg.emit("오디오 추출 중...")
                if subclip.audio:
                    subclip.audio.write_audiofile(self.output_path, logger=my_logger)
            
            elif self.mode == 'video':
                if self.crop_rect:
                    rx, ry, rw, rh = self.crop_rect
                    w, h = subclip.size
                    x1 = int(rx * w)
                    y1 = int(ry * h)
                    x2 = int((rx + rw) * w)
                    y2 = int((ry + rh) * h)
                    self.status_msg.emit("크롭 적용 중...")
                    subclip = subclip.crop(x1=x1, y1=y1, x2=x2, y2=y2)

                self.status_msg.emit("렌더링 시작...")
                subclip.write_videofile(self.output_path, codec='libx264', audio_codec='aac', logger=my_logger)
            
            clip.close()
            self.progress_val.emit(100)
            self.status_msg.emit("완료!")
            self.finished_signal.emit()
            
        except Exception as e:
            self.status_msg.emit(f"에러: {str(e)}")
            self.finished_signal.emit()

# ==========================================
# 3. 비디오 & 오버레이 시스템 (수정됨)
# ==========================================
class CropOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # 마우스 이벤트를 받기 위해 투명하지만 윈도우처럼 동작하게 설정
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 중요: 오버레이가 마우스 이벤트를 무시하지 않도록 설정
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False) 
        self.setStyleSheet("background: transparent;")
        
        self.origin = QPoint()
        self.rect_geometry: QRect | None = None
        self.drawing = False
        self.crop_enabled = False

    def set_mode(self, enabled):
        self.crop_enabled = enabled
        if enabled:
            # 활성화 시 중앙에 힌트 박스 표시 (사용자가 인식하기 쉽게)
            w, h = self.width(), self.height()
            if w > 0 and h > 0:
                self.rect_geometry = QRect(int(w*0.3), int(h*0.3), int(w*0.4), int(h*0.4))
        else:
            self.rect_geometry = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if not self.crop_enabled: return 
        
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.rect_geometry = QRect(self.origin, QSize())
            self.drawing = True
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self.crop_enabled: return

        if self.drawing:
            # 현재 마우스 위치까지 사각형 갱신
            current_pos = event.position().toPoint()
            self.rect_geometry = QRect(self.origin, current_pos).normalized()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if not self.crop_enabled: return

        if event.button() == Qt.MouseButton.LeftButton:
            self.drawing = False
            if self.rect_geometry is not None:
                # 너무 작은 사각형은 취소 (오클릭 방지)
                if self.rect_geometry.width() < 10 or self.rect_geometry.height() < 10:
                    self.rect_geometry = None
            self.update()

    def paintEvent(self, event):
        # 모드가 켜져있고 사각형이 있을 때만 그림
        if self.crop_enabled and self.rect_geometry is not None:
            painter = QPainter(self)
            # 펜: 빨간색 실선, 두께 3
            pen = QPen(Qt.GlobalColor.red, 3, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            # 브러시: 빨간색인데 투명도 30% (내부가 살짝 비침)
            painter.setBrush(QColor(255, 0, 0, 50))
            painter.drawRect(self.rect_geometry)

    def get_normalized_rect(self):
        if not self.crop_enabled or self.rect_geometry is None: return None
        w, h = self.width(), self.height()
        if w == 0 or h == 0: return None
        r = self.rect_geometry
        # 전체 해상도 대비 비율로 반환 (0.0 ~ 1.0)
        return (r.x() / w, r.y() / h, r.width() / w, r.height() / h)


class VideoContainer(QWidget):
    """QVideoWidget 위에 CropOverlay를 자식으로 붙여서 항상 위에 표시되게 함"""
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 1. 비디오 위젯 생성
        self.video_widget = QVideoWidget(self)
        self.video_widget.setStyleSheet("background-color: black;")
        
        # 2. 오버레이 생성 
        # [중요 변경점] 오버레이의 부모를 'video_widget'으로 설정하여 비디오 위에 붙어다니게 함
        self.overlay = CropOverlay(self.video_widget)
        self.overlay.raise_() # 맨 앞으로 가져오기

    def resizeEvent(self, event):
        # 컨테이너 크기가 변하면 비디오 위젯 크기 조절
        size = self.size()
        self.video_widget.resize(size)
        
        # 오버레이는 비디오 위젯 크기에 딱 맞춤
        self.overlay.resize(self.video_widget.size())
        super().resizeEvent(event)

    def get_video_output(self):
        return self.video_widget

    def set_crop_mode(self, enabled):
        self.overlay.set_mode(enabled)
        # 모드 변경 시 오버레이 강제 갱신
        self.overlay.raise_()
        self.overlay.update()

    def get_crop_rect(self):
        return self.overlay.get_normalized_rect()

# ==========================================
# 4. 메인 윈도우
# ==========================================
class SolCutter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SolCutter - Video Editor")
        
        self.settings = QSettings("MySoft", "SolCutter")
        self.load_window_settings()
        
        self.video_path = ""
        self.duration = 0
        self.start_trim = 0.0
        self.end_trim = 0.0

        self.init_ui()
        self.init_player()

    def load_window_settings(self):
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1000, 800)

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
    
    # [추가] 안전하게 아이콘 가져오는 헬퍼 함수
    def get_std_icon(self, icon_name):
        style = self.style()
        if style:
            return style.standardIcon(icon_name)
        return QIcon()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 1. 상단 파일 로드
        top_layout = QHBoxLayout()
        self.btn_open = QPushButton("파일 열기")
        self.btn_open.clicked.connect(self.open_file)
        self.lbl_status = QLabel("파일을 불러와주세요.")
        self.lbl_status.setStyleSheet("color: gray;")
        top_layout.addWidget(self.btn_open)
        top_layout.addWidget(self.lbl_status)
        top_layout.addStretch()

        # 2. 비디오 컨테이너
        self.video_container = VideoContainer()
        
        # 자르기 버튼
        crop_control_layout = QHBoxLayout()
        self.btn_crop_toggle = QPushButton("✂️ 자르기 모드 (OFF)")
        self.btn_crop_toggle.setCheckable(True)
        self.btn_crop_toggle.setEnabled(False)
        self.btn_crop_toggle.clicked.connect(self.toggle_crop_mode)
        self.btn_crop_toggle.setStyleSheet("""
            QPushButton { background-color: #f0f0f0; padding: 5px; }
            QPushButton:checked { background-color: #ffcccc; border: 2px solid red; font-weight: bold; }
        """)
        crop_control_layout.addWidget(self.btn_crop_toggle)
        crop_control_layout.addStretch()

        # 3. 재생 컨트롤
        control_layout = QHBoxLayout()
        self.btn_play = QPushButton()
        # [수정] Pylance 오류 방지를 위해 헬퍼 함수 사용
        self.btn_play.setIcon(self.get_std_icon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.clicked.connect(self.play_video)
        self.btn_play.setEnabled(False)
        
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.set_position)
        self.lbl_current_time = QLabel("00:00")
        self.lbl_total_time = QLabel("00:00")
        control_layout.addWidget(self.btn_play)
        control_layout.addWidget(self.lbl_current_time)
        control_layout.addWidget(self.slider)
        control_layout.addWidget(self.lbl_total_time)

        # 4. Trim
        trim_layout = QHBoxLayout()
        self.btn_set_start = QPushButton("시작점 설정")
        self.btn_set_start.clicked.connect(self.set_start_point)
        self.btn_set_end = QPushButton("종료점 설정")
        self.btn_set_end.clicked.connect(self.set_end_point)
        self.lbl_trim_info = QLabel("구간: 전체")
        self.lbl_trim_info.setStyleSheet("color: #2c3e50; font-weight: bold;")
        self.btn_reset_trim = QPushButton("초기화")
        self.btn_reset_trim.clicked.connect(self.reset_trim)
        trim_layout.addWidget(self.btn_set_start)
        trim_layout.addWidget(self.btn_set_end)
        trim_layout.addWidget(self.lbl_trim_info)
        trim_layout.addWidget(self.btn_reset_trim)
        trim_layout.addStretch()

        # 5. Export
        export_layout = QVBoxLayout()
        btn_layout = QHBoxLayout()
        self.btn_save_video = QPushButton("영상 저장 (Crop + Trim)")
        self.btn_save_video.clicked.connect(lambda: self.export_media('video'))
        self.btn_save_video.setEnabled(False)
        self.btn_save_video.setStyleSheet("background-color: #d1e7dd; height: 35px; font-weight: bold;")
        
        self.btn_save_audio = QPushButton("오디오 추출 (mp3)")
        self.btn_save_audio.clicked.connect(lambda: self.export_media('audio'))
        self.btn_save_audio.setEnabled(False)
        self.btn_save_audio.setStyleSheet("height: 35px;")

        btn_layout.addWidget(self.btn_save_video)
        btn_layout.addWidget(self.btn_save_audio)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("QProgressBar { text-align: center; } QProgressBar::chunk { background-color: #05B8CC; }")

        main_layout.addLayout(top_layout)
        main_layout.addWidget(self.video_container, stretch=1)
        main_layout.addLayout(crop_control_layout)
        main_layout.addLayout(control_layout)
        main_layout.addLayout(trim_layout)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(line)
        main_layout.addLayout(export_layout)

    def init_player(self):
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_container.get_video_output())
        
        self.media_player.playbackStateChanged.connect(self.media_state_changed)
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.errorOccurred.connect(self.handle_errors)

    def open_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "비디오 열기", "", "Video Files (*.mp4 *.avi *.mkv)")
        if file_name:
            self.video_path = file_name
            self.lbl_status.setText(f"파일: {os.path.basename(file_name)}")
            
            self.media_player.setSource(QUrl.fromLocalFile(file_name))
            
            self.btn_play.setEnabled(True)
            self.btn_save_video.setEnabled(True)
            self.btn_save_audio.setEnabled(True)
            self.btn_crop_toggle.setEnabled(True) 
            self.btn_crop_toggle.setChecked(False)
            self.toggle_crop_mode()
            
            self.start_trim = 0.0
            self.end_trim = 0.0
            
            self.media_player.play()
            self.media_player.pause()

    def toggle_crop_mode(self):
        is_on = self.btn_crop_toggle.isChecked()
        self.video_container.set_crop_mode(is_on)
        
        if is_on:
            self.btn_crop_toggle.setText("✂️ 자르기 모드 (ON)")
            self.lbl_status.setText("화면을 드래그하여 자를 영역을 선택하세요.")
        else:
            self.btn_crop_toggle.setText("✂️ 자르기 모드 (OFF)")
            self.lbl_status.setText("준비 완료")

    def play_video(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def media_state_changed(self, state):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setIcon(self.get_std_icon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.btn_play.setIcon(self.get_std_icon(QStyle.StandardPixmap.SP_MediaPlay))

    def position_changed(self, position):
        if not self.slider.isSliderDown():
            self.slider.setValue(position)
        self.lbl_current_time.setText(self.format_time(position))

    def duration_changed(self, duration):
        self.slider.setRange(0, duration)
        self.duration = duration
        self.lbl_total_time.setText(self.format_time(duration))

    def set_position(self, position):
        self.media_player.setPosition(position)

    def handle_errors(self):
        self.btn_play.setEnabled(False)
        err_msg = self.media_player.errorString()
        self.lbl_status.setText(f"재생 에러: {err_msg}")

    def set_start_point(self):
        self.start_trim = self.media_player.position() / 1000.0
        self.update_trim_label()

    def set_end_point(self):
        self.end_trim = self.media_player.position() / 1000.0
        self.update_trim_label()

    def reset_trim(self):
        self.start_trim = 0.0
        self.end_trim = 0.0
        self.update_trim_label()

    def update_trim_label(self):
        s_txt = self.format_time(int(self.start_trim * 1000))
        e_txt = self.format_time(int(self.end_trim * 1000)) if self.end_trim > 0 else "끝"
        self.lbl_trim_info.setText(f"구간: {s_txt} ~ {e_txt}")

    def export_media(self, mode):
        if not self.video_path: return
        
        default_name = "output.mp4"
        if mode == 'audio':
            date_str = datetime.now().strftime("%Y%m%d")
            default_name = f"{date_str}_1.mp3"
            
        ext_filter = "MP4 Files (*.mp4)" if mode == 'video' else "MP3 Files (*.mp3)"
        
        output_path, _ = QFileDialog.getSaveFileName(self, "저장", default_name, ext_filter)
        if not output_path: return

        self.set_ui_locked(True)
        self.progress_bar.setValue(0)

        crop_rect = self.video_container.get_crop_rect()
        self.exporter = ExportThread(self.video_path, output_path, self.start_trim, self.end_trim, crop_rect, mode)
        
        self.exporter.status_msg.connect(self.lbl_status.setText)
        self.exporter.progress_val.connect(self.progress_bar.setValue)
        self.exporter.finished_signal.connect(self.export_finished)
        self.exporter.start()

    def export_finished(self):
        self.set_ui_locked(False)
        QMessageBox.information(self, "완료", "저장이 완료되었습니다!")
        self.lbl_status.setText("준비 완료")
        self.progress_bar.setValue(100)

    def set_ui_locked(self, locked):
        self.btn_open.setDisabled(locked)
        self.btn_save_video.setDisabled(locked)
        self.btn_save_audio.setDisabled(locked)
        self.btn_crop_toggle.setDisabled(locked)
        self.video_container.setDisabled(locked)

    @staticmethod
    def format_time(ms):
        seconds = (ms // 1000) % 60
        minutes = (ms // 60000) % 60
        hours = (ms // 3600000)
        if hours > 0: return f"{hours:02}:{minutes:02}:{seconds:02}"
        return f"{minutes:02}:{seconds:02}"

def load_custom_font(app):
    font_path = os.path.join("source", "Pretendard-SemiBold.otf")
    if os.path.exists(font_path):
        font_id = QFontDatabase.addApplicationFont(font_path)
        if font_id != -1:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                app.setFont(QFont(families[0], 10))

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    load_custom_font(app)
    window = SolCutter()
    window.show()
    sys.exit(app.exec())