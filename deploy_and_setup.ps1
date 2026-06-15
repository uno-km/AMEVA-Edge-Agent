# AMEVA Edge Agent - 원터치 자동 배포 및 빌드/설치 스크립트
# Usage: .\deploy_and_setup.ps1 -sshHost 192.168.0.220 -sshPort 8022 -sshUser a0_a30 -sshKey C:\Users\ATSAdmin\.ssh\id_ed25519

param (
    [string]$sshHost = "192.168.0.220",
    [int]$sshPort = 8022,
    [string]$sshUser = "a0_a30",
    [string]$sshKey = ""
)

$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "      AMEVA Edge Agent - 원터치 무인 설치 셋팅      " -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

# 1. SSH 키 감지 및 확인
if ([string]::IsNullOrEmpty($sshKey)) {
    $defaultEd25519 = "C:\Users\ATSAdmin\.ssh\id_ed25519"
    $defaultRsa = "C:\Users\ATSAdmin\.ssh\id_rsa"
    
    if (Test-Path $defaultEd25519) {
        $sshKey = $defaultEd25519
    } elseif (Test-Path $defaultRsa) {
        $sshKey = $defaultRsa
    } else {
        Write-Host "[SSH] SSH 키쌍이 발견되지 않았습니다. 새로운 ed25519 키쌍을 생성합니다..." -ForegroundColor Yellow
        ssh-keygen -t ed25519 -N "" -f $defaultEd25519
        $sshKey = $defaultEd25519
    }
}

Write-Host "[인증] 사용할 SSH Private Key: $sshKey" -ForegroundColor Gray

# 2. 공개키 경로 찾기 및 내용 읽기
$sshKeyPub = "${sshKey}.pub"
if (-not (Test-Path $sshKeyPub)) {
    Write-Error "SSH 공개키 파일($sshKeyPub)을 찾을 수 없습니다."
    exit 1
}

$pubKeyContent = (Get-Content -Path $sshKeyPub -Raw).Trim()

# 3. 갤럭시 기기에 SSH 공개키 자동 등록 (최초 1회 비밀번호 입력 필요)
Write-Host "[인증] 갤럭시 기기($sshUser@${sshHost}:${sshPort})에 SSH 공개키를 등록합니다..." -ForegroundColor Yellow
Write-Host "[안내] 기기 비밀번호를 입력하라는 메시지가 표시될 수 있습니다." -ForegroundColor Gray

$remoteCmd = "mkdir -p ~/.ssh && echo '$pubKeyContent' >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"

ssh -p $sshPort -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$sshUser@$sshHost" $remoteCmd

if ($LASTEXITCODE -ne 0) {
    Write-Warning "SSH 키 등록 명령이 실패했거나 취소되었습니다. 기기에 이미 키가 등록되어 있는 경우 계속 진행됩니다."
} else {
    Write-Host "[성공] SSH 공개키 등록 완료!" -ForegroundColor Green
}

# 4. 호스트 deploy 명령어 실행 (setup.sh 자동 실행 옵션 포함)
Write-Host "[배포] 모바일 기기로 소스 주입 및 무인 설치(setup.sh) 시작..." -ForegroundColor Cyan

# 현재 작업 경로 확인 및 필요 시 host 폴더로 이동하여 실행
$currentDir = Get-Location
if ($currentDir.Path.EndsWith("host")) {
    python main_host.py deploy --mode dev --ssh-host $sshHost --ssh-port $sshPort --ssh-user $sshUser --ssh-key $sshKey --run-setup
} else {
    python host/main_host.py deploy --mode dev --ssh-host $sshHost --ssh-port $sshPort --ssh-user $sshUser --ssh-key $sshKey --run-setup
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "배포 및 설치 작업이 실패했습니다. 로그를 확인해 주세요."
    exit 1
}

Write-Host ""
Write-Host "===================================================" -ForegroundColor Green
Write-Host "      AMEVA Edge Agent 원터치 배포/설치 완료!        " -ForegroundColor Green
Write-Host "===================================================" -ForegroundColor Green
Write-Host "갤럭시 기기에서 에이전트를 구동하려면 SSH 세션에서 다음을 실행하세요:"
Write-Host "  cd ~/dev/ameva-agent"
Write-Host "  ./run.sh"
Write-Host "===================================================" -ForegroundColor Green
