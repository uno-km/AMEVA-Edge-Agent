import sqlite3, os

def main():
    conn = sqlite3.connect('../data/all_agent_master.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM jobs')
    rows = c.fetchall()
    conn.close()

    print('\n' + '='*80)
    print('                  AMEVA EDGE AGENT DETAIL REPORT')
    print('='*80)

    for r in rows:
        audio_name = os.path.basename(r['original_audio_path']) if r['original_audio_path'] else 'None'
        print(f'\n[작업 ID: {r["id"]}] {audio_name}')
        print('-'*80)
        print(f'* STT 모델 : {r["stt_model"] or "N/A"}')
        print(f'  - 처리 시간: {r["stt_started_at"]} ~ {r["stt_ended_at"]}')
        print(f'* LLM 모델 : {r["llm_model"] or "N/A"}')
        print(f'  - 처리 시간: {r["llm_started_at"]} ~ {r["llm_ended_at"]}')
        print(f'* 동기화   : {r["sync_method"]} / 상태: {r["sync_status"]}')
        print('* [요약 결과 (Summary)] :')
        
        summary_path = r['summary_path']
        summary_text = '요약 파일 없음'
        if summary_path:
            local_path = os.path.join('../data/incoming/files', os.path.basename(summary_path))
            if os.path.exists(local_path):
                with open(local_path, 'r', encoding='utf-8') as f:
                    summary_text = f.read().strip()
                    
        print(summary_text)
        print('='*80)

if __name__ == '__main__':
    main()
