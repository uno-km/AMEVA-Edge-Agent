import os
import subprocess
import shutil
import json
from datetime import datetime
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
                    # 실패 시 DB 상태를 FAILED로 변경하지 않고 기존 PENDING으로 두어 언제든지 재시도할 수 있도록 함
                    # self.db.update_status(job['id'], 'FAILED', error_message=str(exc))

        print(f"[STTEngine] STT 처리 완료. 성공: {success_count}/{len(jobs)}")
        return success_count

    def _process_single_job(self, job):
        """개별 음성 파일에 대해 오디오 변환, 전처리 및 정밀 타임스탬프 STT 실행을 수행합니다."""
        job_id = job['id']
        original_path = job['original_audio_path']
        
        if not os.path.exists(original_path):
            error_msg = f"원본 파일이 존재하지 않습니다: {original_path}"
            print(f"[STTEngine] {error_msg}")
            # self.db.update_status(job_id, 'FAILED', error_message=error_msg)
            return False

        print(f"[STTEngine] 작업 시작 [ID {job_id}]: {original_path}")
        
        stt_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.db.update_status(job_id, status='PROCESSING_STT', stt_started_at=stt_started_at)
        
        # 1. 오디오 포맷 변환 및 경로 정의
        filename = os.path.basename(original_path)
        base_name, _ = os.path.splitext(filename)
        
        # 보존용 오디오 파일 (원본 배경 소음을 포함한 일반 변환 WAV)
        wav_path = os.path.join(config.audio_dir, f"converted_{base_name}_{job_id}.wav")
        # STT 추론용 전처리 오디오 파일 (노이즈 필터링 및 무음부 소거)
        cleaned_wav_path = os.path.join(config.audio_dir, f"cleaned_{base_name}_{job_id}.wav")
        
        try:
            # 1a. 보존용 원본 WAV 변환 (배경 소음 유지)
            self._convert_to_wav(original_path, wav_path, clean=False)
            
            # 1b. STT 추론 전용 클린 오디오 변환 (노이즈 및 무음 전처리 적용)
            self._convert_to_wav(original_path, cleaned_wav_path, clean=True)
        except Exception as e:
            error_msg = f"WAV 변환/전처리 오류: {e}"
            print(f"[STTEngine] [ID {job_id}] {error_msg}")
            # self.db.update_status(job_id, 'FAILED', error_message=error_msg)
            # 리소스 정리
            for p in [wav_path, cleaned_wav_path]:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except:
                        pass
            return False

        # 2. whisper.cpp 실행 및 정밀 타임스탬프 텍스트 가공
        stt_output_base = os.path.join(config.stt_dir, f"stt_{base_name}_{job_id}")
        stt_json = f"{stt_output_base}.json"
        stt_path = f"{stt_output_base}.txt"

        try:
            # 전처리된 클린 오디오를 기반으로 Whisper STT 수행 (JSON 결과 생성)
            self._run_whisper_cpp(cleaned_wav_path, stt_output_base)
            
            if not os.path.exists(stt_json):
                raise FileNotFoundError(f"STT JSON 결과 파일이 생성되지 않았습니다: {stt_json}")
            
            # JSON 데이터를 파싱하여 정밀 타임스탬프 [시:분:초.밀리초] 대사 포맷으로 변환
            with open(stt_json, "r", encoding="utf-8") as f:
                whisper_data = json.load(f)
                
            segments = whisper_data.get("transcription", [])
            formatted_lines = []
            
            for seg in segments:
                offsets = seg.get("offsets", {})
                start_ms = offsets.get("from", 0)
                end_ms = offsets.get("to", 0)
                
                # offsets 부재 시 초 단위 필드 백업 폴백
                if not offsets:
                    start_ms = seg.get("start", 0) * 1000
                    end_ms = seg.get("end", 0) * 1000
                    
                start_str = self._format_timestamp(start_ms)
                end_str = self._format_timestamp(end_ms)
                text = seg.get("text", "").strip()
                
                if text:
                    # [시:분:초.밀리초 -> 시:분:초.밀리초] 대사 형식으로 기록
                    formatted_lines.append(f"[{start_str} -> {end_str}] {text}")
            
            # 최종 텍스트 파일로 저장
            with open(stt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(formatted_lines))
                
            # DB 상태 업데이트
            stt_ended_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.db.update_status(
                job_id=job_id,
                status='STT_COMPLETED',
                wav_path=wav_path, # DB에는 외부 소음이 보존된 converted WAV 경로를 등록!
                stt_path=stt_path,
                stt_ended_at=stt_ended_at
            )
            print(f"[STTEngine] [ID {job_id}] 성공적으로 변환 완료 -> {stt_path}")
            return True
            
        except Exception as e:
            error_msg = f"STT 추론/파싱 중 오류 발생: {e}"
            print(f"[STTEngine] [ID {job_id}] {error_msg}")
            # self.db.update_status(job_id, 'FAILED', error_message=error_msg)
            # 실패 시 변환 파일 소거 시도
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except:
                    pass
            return False
            
        finally:
            # 임시 추론용 클린 오디오 및 가공용 JSON 파일 소거
            for temp_file in [cleaned_wav_path, stt_json]:
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except:
                        pass

    def _convert_to_wav(self, input_path, output_path, clean=False):
        """ffmpeg 명령을 통해 오디오 파일을 whisper.cpp 규격(16kHz, Mono, PCM-16bit)의 wav로 변환합니다."""
        # 데스크톱 모의 검증 또는 ffmpeg가 없는 경우를 위한 목업 처리
        if config.whisper_bin == "mock" or not shutil.which("ffmpeg"):
            print(f"[STTEngine] [MOCK] ffmpeg 모의 처리 (clean={clean}): {input_path} -> {output_path}")
            # 빈 파일 생성
            with open(output_path, "wb") as f:
                f.write(b"MOCK WAV CONTENT")
            return

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
        ]
        
        if clean:
            # -af 필터 체인: highpass(저역 웅웅거림 필터), lowpass(고주파 노이즈 제거), afftdn(FFT 노이즈 감소), volume(증폭), silenceremove(무음부 소거)
            cmd.extend([
                "-af", "highpass=f=80, lowpass=f=8000, afftdn, volume=1.5, silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB"
            ])
            
        cmd.extend([
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            output_path
        ])
        
        # 프로세스 실행
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 에러 코드 {result.returncode}. 에러 로그: {result.stderr}")

    def _run_whisper_cpp(self, wav_path, output_base_path):
        """whisper.cpp 바이너리를 사용하여 STT 텍스트를 JSON 형태로 추출합니다."""
        # 데스크톱 모의 검증 또는 바이너리가 없는 경우를 위한 목업 처리
        if config.whisper_bin == "mock" or not os.path.exists(config.whisper_bin):
            mock_json_file = f"{output_base_path}.json"
            print(f"[STTEngine] [MOCK] whisper.cpp 모의 처리: {wav_path} -> {mock_json_file}")
            mock_data = {
                "transcription": [
                    {
                        "offsets": {
                            "from": 1200,
                            "to": 5400
                        },
                        "text": "안녕하세요, AMEVA 에이전트 테스트 음성입니다."
                    },
                    {
                        "offsets": {
                            "from": 6000,
                            "to": 11500
                        },
                        "text": "갤럭시 A35 기기 로컬 환경에서 구동하는 STT 성능이 아주 납득할만 하군요."
                    }
                ]
            }
            with open(mock_json_file, "w", encoding="utf-8") as f:
                json.dump(mock_data, f, ensure_ascii=False, indent=2)
            return

        cmd = [
            config.whisper_bin,
            "-m", config.whisper_model,
            "-f", wav_path,
            "-oj",
            "-of", output_base_path
        ]

        if config.whisper_max_len > 0:
            cmd.extend(["-ml", str(config.whisper_max_len), "-sow"])
            
        if config.whisper_ko:
            cmd.extend(["-l", "ko"])

        # whisper.cpp 명령어 실행 (실시간 로그 출력을 위해 파이프 대신 콘솔 상속)
        result = subprocess.run(cmd, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"whisper.cpp 에러 코드 {result.returncode}.")

    def _format_timestamp(self, ms):
        """밀리초 단위를 [시:분:초.밀리초] 형식으로 변환합니다."""
        total_sec = ms / 1000.0
        h = int(total_sec // 3600)
        m = int((total_sec % 3600) // 60)
        s = int(total_sec % 60)
        msec = int(ms % 1000)
        return f"{h:02d}:{m:02d}:{s:02d}.{msec:03d}"
