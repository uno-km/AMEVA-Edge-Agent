# AMEVA Edge Agent

> **[프로젝트 요약 (Resume Profile)]**
> 
> ```mermaid
> flowchart LR
>     A["Data Acquisition<br/>(📍 HERE)"]:::current --> B["Data Processing<br/>& Storage"]
>     B --> C["Model Training"]
>     C --> D["Model Deployment"]
>     D --> E["Monitoring"]
>     classDef current fill:#000,stroke:#333,stroke-width:2px,color:#fff;
> ```
> 
> * **① 제목:** MLOps 모바일 엣지 데이터 수집 파이프라인 (AMEVA Edge Agent)
> * **② 주제:** 
>   * MLOps 파이프라인의 데이터 수집(Data Acquisition) 노드로서, 모바일 엣지 환경에서 오디오 데이터 수집부터 STT/LLM 전처리 및 호스트 동기화까지 수행하는 보안 지향적 아키텍처 구축
>   * **연구 가설 실증**: "엣지 디바이스의 독립적 컴퓨팅 자원을 활용하여 STT/LLM 추론을 선행할 경우 메인 서버의 전처리 부하를 분산할 수 있을 것인가?"에 대한 하드웨어별(Galaxy S20 vs A35) 성능 한계 및 타당성 분석
> * **③ 내용요지:**
>   * **사용 기술:** `Python 3` (Standard Library Only), `SQLite3`, `Shell Scripting`, `SSH/SCP Tunneling`
>   * **사용 모델:** `Whisper.cpp (Small)` (STT), `BitNet-b1.58-2B-4T (GGUF, i2_s)` (LLM)
>   * **보안 아키텍처:** 호스트 주도형 동적 페이로드 주입(Payload Injection), 쉘 히스토리 차단(`HISTFILE=/dev/null`) 및 임시 은닉 폴더(`.sys_cache`) 기반 스크립트 실행(Zero-Footprint), 평문 키리스(Keyless) 환경 변수 동적 주입, 데이터 식별자 난수화(UUID), 4096 bytes 청크 단위 난수(`/dev/urandom`) 3회 덮어쓰기(Secure Erase), 프로세스 강제 종료 감지 자폭(Self-Destruct) 메커니즘, 데이터 동기화 완료 후 즉각적인 원본 소거 및 호스트의 원격 파쇄(Remote Shred) 명령 지원
> * **④ 기여도:** 단독 개발 (100%)

---

## 1. 프로젝트 비전 (MLOps Data Acquisition)

**AMEVA Edge Agent**는 MLOps 파이프라인에서 **초기 데이터 수집(Data Acquisition)**을 담당하는 엣지 노드입니다. 
단순한 데이터 수집을 넘어, 엣지(Edge) 환경에서 1차적인 AI 추론(STT 및 텍스트 요약)을 수행하여 **메인 서버의 전처리 부하를 분산하는 아키텍처**를 목표로 합니다.
현재는 음성(Audio) 데이터 파이프라인을 구축하였으나, 향후 이미지 및 비디오 전처리 결과를 메인 서버로 배치(Batch) 동기화하는 범용 수집 모듈로 확장 가능하도록 설계되었습니다.

---

## 2. 연구: 엣지 AI 오프로딩 타당성 및 벤치마크 (S20 vs A35)

본 프로젝트의 주요 연구 과제는 다음과 같습니다.
> *"모바일 디바이스의 가용 컴퓨팅 자원을 활용하여 엣지 환경에서 STT와 LLM 추론을 독립적으로 수행하는 것이 전처리 부하 분산에 효율적인가?"*

이를 검증하기 위해 하이엔드 구형 기기인 **Galaxy S20**과 최신 중급기인 **Galaxy A35**를 대상으로 엣지 AI 구동 성능 및 한계를 벤치마크 하였습니다.

### 2.1 하드웨어 비교 및 추론 성능
두 기기 간의 추론 성능(Token generation speed 및 STT 변환 속도)을 비교한 결과는 다음과 같습니다.
- **Galaxy A35 (Exynos 1380)**: 
  - ARM NEON DotProd 최적화 및 C++ 커널 직접 수정을 통해 BitNet 1.58b 모델에서 **3.01 tokens/sec**의 추론 속도 달성. 
  - 발열 제어 및 전력 효율이 상대적으로 우수하여 연속적인 배치 파일 처리에 안정적인 성능 유지.
- **Galaxy S20 (Snapdragon 865)**: 
  - 단일 작업에 대한 순간 추론 속도는 높으나, 지속적인 부하가 가해질 경우 스로틀링(Throttling) 현상 발생. 
  - 제한적인 가용 RAM(6GB~8GB) 환경에서 대규모 배치 작업 시 OOM(Out of Memory) 발생 빈도가 높음.

### 2.2 대규모 배치(Batch) 처리 한계 및 결론
일 평균 200~300개의 음성 파일(개당 10~20분 분량)을 엣지 노드에서 전처리할 때의 자원 소모량을 분석하였습니다.
- **예상 소요 시간**: 파일당 평균 3~5분이 소요되며, 300개 처리 시 기기 컴퓨팅 자원의 상당 부분을 장시간 점유하게 됩니다.
- **OOM(Out of Memory) 문제**: 모바일 환경에서 Whisper와 LLM 모델을 반복적으로 메모리에 로드/언로드 하는 과정에서 OOM 발생 리스크가 존재합니다.
- **최종 결론**: **"최신 중급기 이상의 디바이스에서 소형 양자화 모델(1.58b~3b)을 구동하여 서버 전처리를 분산하는 것은 아키텍처 관점에서 유의미하나, 저가형 또는 구형 모델에서 엣지 추론을 수행하는 것은 메모리 병목 및 스로틀링으로 인해 시스템 불안정을 초래할 수 있다."**

---

## 3. 데이터 동기화 및 마이그레이션 성능

엣지에서 전처리된 데이터(원본 오디오, STT 전사본, 요약본, SQLite DB)는 지정된 스케줄(21시, 23시)에 호스트 서버로 동기화됩니다.
- **전송 아키텍처**: SSH 터널링 기반 SCP 비동기 배치 전송.
- **무결성 검증**: 원격지 전송 완료 후 파일 크기 기반 교차 검증을 수행하며, 타임아웃(120초) 로직을 통해 불안정한 모바일 네트워크 환경에 대응합니다.

---

## 4. 엣지 디바이스 보안 조치 (Anti-Forensics Architecture)

물리적 엣지 디바이스는 기기 분실 및 비인가 접근 리스크가 존재합니다. 이를 방어하기 위해 운영(PRD) 모드 배포 시 다음과 같은 보안 로직이 적용됩니다.

1. **Zero-Footprint 메모리 로드**
   - 디바이스 로컬 저장소에 소스 코드를 보관하거나 `.env` 등 평문 설정 파일을 생성하지 않습니다.
   - 호스트 PC가 실행 시점에 스크립트를 임시 디렉토리에 동적으로 할당하고 실행하며, 쉘 명령어 기록(`HISTFILE`)을 비활성화합니다.
2. **파일명 및 데이터 식별자 난수화**
   - 처리 중인 파일명("stt", "요약" 등)에 직관적인 텍스트를 사용하지 않고 `UUID4` 기반의 난수로 식별자를 대체하여 데이터의 목적을 보호합니다.
3. **데이터 무효화 및 안전 소거 (Secure Erase)**
   - 호스트 서버로 데이터 동기화가 완료되거나, 프로세스 강제 종료(SIGINT/SIGTERM) 이벤트가 감지될 경우 데이터 소거 로직이 작동합니다.
   - 파일 시스템 블록 단위(4096 bytes)로 `/dev/urandom` 난수를 파일 크기만큼 3회 이상 덮어쓰기하여 디스크 포렌식을 통한 데이터 복원 가능성을 최소화합니다.

---

## 5. 연락처 (Contact)

- **GitHub**: [@uno-km](https://github.com/uno-km)
- **Email**: zhfldk014745@naver.com
- **Research Focus**: MLOps Data Acquisition, Edge-native Inference, Data Sovereignty, Anti-Forensics Architecture

*Last Updated: June 15, 2026*
