import os
import subprocess
import tempfile
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.config import config
from src.db import DBManager

class STTEngine:
    def __init__(self, db_manager=None):
        self.db = db_manager or DBManager()

    def process_all_pending(self):
        """PENDING 상태인 모든 작업을 가져와 STT 처리를 수행합니다."""
        jobs = self.db.get_pending_stt_jobs()
        if not jobs:
            print("[STTEngine] 처리할 PENDING 상태의 작업이 없습니다.")
            return 0

        print(f"[STTEngine] 총 {len(jobs)}개의 대기 중인 작업을 STT 처리 시작합니다. (병렬 인원수: {config.max_workers})")

        success_count = 0
        
        # ThreadPoolExecutor를 사용해 병렬/직렬 처리 수행
        with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
            future_to_job = {executor.submit(self._process_single_job, job): job for job in jobs}
            for future in as_completed(future_to_job):
                job = future_to_job[future]
                try:
                    result = future.result()
                    if result:
                        success_count += 1
                except Exception as exc:
                    print(f"[STTEngine] 작업 처리 중 예외 발생 (ID: {job['id']}): {exc}")
                    self.db.update_status(job['id'], 'FAILED', error_message=str(exc))

        print(f"[STTEngine] STT 처리 완료. 성공: {success_count}/{len(jobs)}")
        return success_count

    def _process_single_job(self, job):
        """개별 음성 파일에 대해 오디오 변환 및 STT 실행을 수행합니다."""
        job_id = job['id']
        original_path = job['original_audio_path']
        
        if not os.path.exists(original_path):
            error_msg = f"원본 파일이 존재하지 않습니다: {original_path}"
            print(f"[STTEngine] {error_msg}")
            self.db.update_status(job_id, 'FAILED', error_message=error_msg)
            return False

        print(f"[STTEngine] 작업 시작 [ID {job_id}]: {original_path}")
        
        # 1. 오디오 포맷 변환 (ffmpeg를 사용해 16kHz, mono, pcm_s16le wav 포맷으로 변환)
        filename = os.path.basename(original_path)
        base_name, _ = os.path.splitext(filename)
        
        # 임시 WAV 파일 경로 지정
        wav_path = os.path.join(config.audio_dir, f"converted_{base_name}_{job_id}.wav")
        
        try:
            self._convert_to_wav(original_path, wav_path)
        except Exception as e:
            error_msg = f"WAV 변환 오류: {e}"
            print(f"[STTEngine] [ID {job_id}] {error_msg}")
            self.db.update_status(job_id, 'FAILED', error_message=error_msg)
            return False

        # 2. whisper.cpp 실행 및 텍스트 추출
        # 출력 베이스 파일 명 (whisper.cpp의 -of 인자는 확장자 없이 제공하면 파일명에 .txt가 붙음)
        stt_output_base = os.path.join(config.stt_dir, f"stt_{base_name}_{job_id}")
        stt_path = f"{stt_output_base}.txt"

        try:
            self._run_whisper_cpp(wav_path, stt_output_base)
            
            # STT 텍스트 파일이 최종 생성되었는지 검증
            if not os.path.exists(stt_path):
                raise FileNotFoundError(f"STT 결과 파일이 생성되지 않았습니다: {stt_path}")
            
            # DB 상태 업데이트
            self.db.update_status(
                job_id=job_id,
                status='STT_COMPLETED',
                wav_path=wav_path,
                stt_path=stt_path
            )
            print(f"[STTEngine] [ID {job_id}] 성공적으로 변환 완료 -> {stt_path}")
            return True
            
        except Exception as e:
            error_msg = f"STT 추론 중 오류 발생: {e}"
            print(f"[STTEngine] [ID {job_id}] {error_msg}")
            # 에러 상태 저장 및 변환용 임시 wav 삭제 시도
            self.db.update_status(job_id, 'FAILED', error_message=error_msg)
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except:
                    pass
            return False

    def _convert_to_wav(self, input_path, output_path):
        """ffmpeg 명령을 통해 오디오 파일을 whisper.cpp 규격(16kHz, Mono, PCM-16bit)의 wav로 변환합니다."""
        # 데스크톱 모의 검증 또는 ffmpeg가 없는 경우를 위한 목업 처리
        if config.whisper_bin == "mock" or not shutil.which("ffmpeg"):
            print(f"[STTEngine] [MOCK] ffmpeg 모의 처리: {input_path} -> {output_path}")
            # 빈 파일 생성
            with open(output_path, "wb") as f:
                f.write(b"MOCK WAV CONTENT")
            return

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            output_path
        ]
        
        # 백그라운드 프로세스로 실행
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 에러 코드 {result.returncode}. 에러 로그: {result.stderr}")

    def _run_whisper_cpp(self, wav_path, output_base_path):
        """whisper.cpp 바이너리를 사용하여 STT 텍스트를 추출합니다."""
        # 데스크톱 모의 검증 또는 바이너리가 없는 경우를 위한 목업 처리
        if config.whisper_bin == "mock" or not os.path.exists(config.whisper_bin):
            mock_text_file = f"{output_base_path}.txt"
            print(f"[STTEngine] [MOCK] whisper.cpp 모의 처리: {wav_path} -> {mock_text_file}")
            with open(mock_text_file, "w", encoding="utf-8") as f:
                f.write(f"이것은 {os.path.basename(wav_path)}에 대한 모의(Mock) STT 인식 결과 텍스트입니다. 대화 내용은 아주 중요합니다.")
            return

        cmd = [
            config.whisper_bin,
            "-m", config.whisper_model,
            "-f", wav_path,
            "-otxt",
            "-of", output_base_path
        ]

        # whisper.cpp 명령어 실행
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"whisper.cpp 에러 코드 {result.returncode}. 에러 로그: {result.stderr}")
