import psycopg2

conn = psycopg2.connect(host='localhost', port=5433, dbname='slm', user='slm_dev', password='slm_dev_1234')
conn.set_client_encoding('UTF8')
cur = conn.cursor()

cur.execute("""
SELECT facilitytype, datainfo, COUNT(*) as cnt
FROM tb_tag_info
WHERE tagtype = 'Analog Input'
  AND datainfo NOT LIKE '%적산%'
GROUP BY facilitytype, datainfo
ORDER BY facilitytype, cnt DESC
LIMIT 100
""")
rows = cur.fetchall()
print("=== facilitytype별 datainfo 분포 ===")
for r in rows:
    print(f"{str(r[0]):15s} | {str(r[1]):35s} | {r[2]}")

print("\n=== facilitytype 종류 ===")
cur.execute("SELECT DISTINCT facilitytype FROM tb_tag_info ORDER BY facilitytype")
for r in cur.fetchall():
    print(r[0])

conn.close()
