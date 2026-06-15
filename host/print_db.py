import sqlite3
import os
import sys

def pad_width(s, width):
    s_str = str(s) if s is not None else "NULL"
    # Calculate visual length: Korean characters count as 2, others as 1
    v_len = sum(2 if ('\uac00' <= c <= '\ud7a3' or '\u3131' <= c <= '\u318e') else 1 for c in s_str)
    padding = max(0, width - v_len)
    return s_str + (' ' * padding)

def truncate_path(path_val, max_len=25):
    if not path_val or path_val == "NULL":
        return "NULL"
    path_str = str(path_val)
    if len(path_str) <= max_len:
        return path_str
    
    # Extract filename or show end of path
    base = os.path.basename(path_str)
    if len(base) > max_len - 4:
        return "..." + base[-(max_len-4):]
    return ".../" + base

def print_pretty_table(db_path, show_all=False):
    if not os.path.exists(db_path):
        print(f"[-] Database file not found: {db_path}")
        return
        
    print(f"\n[+] Database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM jobs")
        rows = cur.fetchall()
        if not rows:
            print("    [!] Table is empty / No rows returned.")
            return
            
        all_cols = list(rows[0].keys())
        
        # Decide which columns to show
        if show_all:
            show_cols = all_cols
        else:
            desired = ['id', 'status', 'sync_status', 'original_audio_path', 'stt_model', 'llm_model', 'created_at']
            show_cols = [col for col in desired if col in all_cols]
        
        # Compute display values with truncation
        display_rows = []
        col_widths = {col: len(col) for col in show_cols}
        
        for row in rows:
            display_row = {}
            for col in show_cols:
                val = row[col]
                val_str = str(val) if val is not None else "NULL"
                if "path" in col:
                    val_str = truncate_path(val_str, 25)
                # Korean character length calculation
                v_len = sum(2 if ('\uac00' <= c <= '\ud7a3' or '\u3131' <= c <= '\u318e') else 1 for c in val_str)
                col_widths[col] = max(col_widths[col], v_len)
                display_row[col] = val_str
            display_rows.append(display_row)
            
        # Limit column widths to prevent excessive wrapping on very long texts (e.g. error messages)
        for col in col_widths:
            col_widths[col] = min(col_widths[col], 30)

        # Draw grid
        top_border = "┌" + "┬".join("─" * (col_widths[col] + 2) for col in show_cols) + "┐"
        header_row = "│" + "│".join(f" {pad_width(col, col_widths[col])} " for col in show_cols) + "│"
        divider = "├" + "┼".join("─" * (col_widths[col] + 2) for col in show_cols) + "┤"
        bottom_border = "└" + "┴".join("─" * (col_widths[col] + 2) for col in show_cols) + "┘"
        
        print(top_border)
        print(header_row)
        print(divider)
        
        for d_row in display_rows:
            row_str = "│"
            for col in show_cols:
                val = d_row[col]
                # Truncate to column limit if it exceeds
                v_len = sum(2 if ('\uac00' <= c <= '\ud7a3' or '\u3131' <= c <= '\u318e') else 1 for c in val)
                if v_len > col_widths[col]:
                    val = val[:col_widths[col]-3] + "..."
                row_str += f" {pad_width(val, col_widths[col])} │"
            print(row_str)
            
        print(bottom_border)
    except Exception as e:
        print(f"[-] Error querying database: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_files = [
        os.path.join(script_dir, 'edge_agent_migrated_localhost_18774.db'),
        os.path.join(script_dir, 'edge_agent_migrated_localhost_12419.db'),
        os.path.join(script_dir, 'data', 'all_agent_master.db'),
        os.path.join(script_dir, 'data', 'incoming', 'db', 'edge_agent_tmp.db')
    ]
    
    show_all = False
    if len(sys.argv) > 1 and sys.argv[1] == "all":
        show_all = True
            
    for db in db_files:
        if os.path.exists(db):
            print_pretty_table(db, show_all)
