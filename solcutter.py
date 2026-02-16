import sys
import os
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QFileDialog, 
                             QSlider, QStyle, QMessageBox, QProgressBar, QFrame)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtCore import Qt, QUrl, QRect, QPoint, QSize, QThread, pyqtSignal, QSettings
from PyQt5.QtGui import QPainter, QPen, QColor, QMouseEvent, QFontDatabase, QFont

# 동영상 처리를 위한 라이브러리
# (MoviePy 2.0 이상인 경우 from moviepy import VideoFileClip 으로 수정)
try:
    from moviepy.editor import VideoFileClip
except ImportError:
    from moviepy import VideoFileClip
    
from proglog import ProgressBarLogger

# ==========================================
# 1. 커스텀 로거 (MoviePy 진행률 캡처용)
# ==========================================
class SolCutterLogger(ProgressBarLogger):
    def __init__(self, update_callback):
        super().__init__()
        self.update_callback = update_callback

    def bars_callback(self, bar, attr, value, old_value=None):
        super().bars_callback(bar, attr, value, old_value)
        if bar == 't' and attr == 'index':
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
            clip = VideoFileClip(self.file_path)

            end = self.end_t if self.end_t > 0 else clip.duration
            end = min(end, clip.duration)
            
            subclip = clip.subclip(self.start_t, end)
            my_logger = SolCutterLogger(self.progress_val.emit)

            if self.mode == 'audio':
                self.status_msg.emit("오디오 추출 중...")
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
# 3. 커스텀 비디오 위젯
# ==========================================
class CropVideoWidget(QVideoWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.origin = QPoint()
        self.rect_geometry = None 
        self.drawing = False

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.origin = event.pos()
            self.rect_geometry = QRect(self.origin, QSize())
            self.drawing = True
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.drawing:
            self.rect_geometry = QRect(self.origin, event.pos()).normalized()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.drawing = False
            if self.rect_geometry and (self.rect_geometry.width() < 10 or self.rect_geometry.height() < 10):
                self.rect_geometry = None
                self.update()

    def paintEvent(self, event):
        super().paintEvent(event) 
        if self.rect_geometry:
            painter = QPainter(self)
            pen = QPen(Qt.red, 2, Qt.SolidLine)
            painter.setPen(pen)
            painter.setBrush(QColor(255, 0, 0, 50)) 
            painter.drawRect(self.rect_geometry)

    def get_normalized_crop_rect(self):
        if not self.rect_geometry: return None
        widget_w = self.width()
        widget_h = self.height()
        if widget_w == 0 or widget_h == 0: return None
        r = self.rect_geometry
        return (r.x() / widget_w, r.y() / widget_h, r.width() / widget_w, r.height() / widget_h)

# ==========================================
# 4. 메인 윈도우
# ==========================================
class SolCutter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SolCutter - Video Editor")
        
        # 1. 설정 초기화 (창 크기 기억)
        self.settings = QSettings("MySoft", "SolCutter")
        self.load_window_settings()
        
        self.video_path = ""
        self.duration = 0
        self.start_trim = 0.0
        self.end_trim = 0.0

        self.init_ui()
        self.init_player()

    def load_window_settings(self):
        # 저장된 geometry가 있으면 복원, 없으면 기본 크기 설정
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1000, 750)

    def closeEvent(self, event):
        # 프로그램 종료 시 현재 창 크기 및 위치 저장
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 상단
        top_layout = QHBoxLayout()
        self.btn_open = QPushButton("파일 열기")
        self.btn_open.clicked.connect(self.open_file)
        self.lbl_status = QLabel("파일을 불러와주세요.")
        self.lbl_status.setStyleSheet("color: gray;")
        top_layout.addWidget(self.btn_open)
        top_layout.addWidget(self.lbl_status)
        top_layout.addStretch()

        # 비디오
        self.video_widget = CropVideoWidget()
        self.video_widget.setStyleSheet("background-color: black;")
        
        # 재생 컨트롤
        control_layout = QHBoxLayout()
        self.btn_play = QPushButton()
        self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.btn_play.clicked.connect(self.play_video)
        self.btn_play.setEnabled(False)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.set_position)
        self.lbl_current_time = QLabel("00:00")
        self.lbl_total_time = QLabel("00:00")
        control_layout.addWidget(self.btn_play)
        control_layout.addWidget(self.lbl_current_time)
        control_layout.addWidget(self.slider)
        control_layout.addWidget(self.lbl_total_time)

        # Trim
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

        # Export
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

        export_layout.addLayout(btn_layout)
        export_layout.addWidget(self.progress_bar)

        main_layout.addLayout(top_layout)
        main_layout.addWidget(self.video_widget, stretch=1)
        main_layout.addLayout(control_layout)
        main_layout.addLayout(trim_layout)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(line)
        main_layout.addLayout(export_layout)

    def init_player(self):
        self.media_player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.stateChanged.connect(self.media_state_changed)
        self.media_player.positionChanged.connect(self.position_changed)
        self.media_player.durationChanged.connect(self.duration_changed)
        self.media_player.error.connect(self.handle_errors)

    def open_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "비디오 열기", "", "Video Files (*.mp4 *.avi *.mkv)")
        if file_name:
            self.video_path = file_name
            self.lbl_status.setText(f"파일: {os.path.basename(file_name)}")
            self.media_player.setMedia(QMediaContent(QUrl.fromLocalFile(file_name)))
            self.btn_play.setEnabled(True)
            self.btn_save_video.setEnabled(True)
            self.btn_save_audio.setEnabled(True)
            self.start_trim = 0.0
            self.end_trim = 0.0
            self.video_widget.rect_geometry = None
            self.video_widget.update()
            self.media_player.play()
            self.media_player.pause()

    def play_video(self):
        if self.media_player.state() == QMediaPlayer.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def media_state_changed(self, state):
        if self.media_player.state() == QMediaPlayer.PlayingState:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        else:
            self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

    def position_changed(self, position):
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
        err_code = self.media_player.error()
        err_msg = self.media_player.errorString()
        if err_code == QMediaPlayer.FormatError:
            msg = "코덱 문제: LAV Filters를 설치해보세요."
        elif err_code == QMediaPlayer.ResourceError:
            msg = "파일 문제: 파일이 없거나 깨졌습니다."
        else:
            msg = f"재생 에러 ({err_code})"
        self.lbl_status.setText(f"{msg} : {err_msg}")

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
        
        # 2. 날짜 기반 기본 파일명 생성 [현재날짜]_1.mp3
        default_name = "output.mp4"
        if mode == 'audio':
            date_str = datetime.now().strftime("%y%m%d")
            default_name = f"{date_str}00.mp3"
            
        ext_filter = "MP4 Files (*.mp4)" if mode == 'video' else "MP3 Files (*.mp3)"
        
        output_path, _ = QFileDialog.getSaveFileName(self, "저장", default_name, ext_filter)
        if not output_path: return

        self.set_ui_locked(True)
        self.progress_bar.setValue(0)

        crop_rect = self.video_widget.get_normalized_crop_rect()
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
        self.video_widget.setDisabled(locked)

    @staticmethod
    def format_time(ms):
        seconds = (ms // 1000) % 60
        minutes = (ms // 60000) % 60
        hours = (ms // 3600000)
        if hours > 0: return f"{hours:02}:{minutes:02}:{seconds:02}"
        return f"{minutes:02}:{seconds:02}"

def load_custom_font(app):
    # 3. 폰트 로드 로직 (source 폴더 확인)
    font_path = os.path.join("source", "Pretendard-SemiBold.otf")
    
    if os.path.exists(font_path):
        font_id = QFontDatabase.addApplicationFont(font_path)
        if font_id != -1:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                app.setFont(QFont(families[0], 10))
                print(f"폰트 로드 성공: {families[0]}")
        else:
            print("폰트 로드 실패: 올바른 폰트 파일인지 확인하세요.")
    else:
        print(f"폰트 파일 없음: {font_path}")
        # 폰트 파일이 없어도 프로그램은 기본 폰트로 실행됨

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    # 폰트 로드 함수 호출
    load_custom_font(app)
    
    window = SolCutter()
    window.show()
    sys.exit(app.exec_())