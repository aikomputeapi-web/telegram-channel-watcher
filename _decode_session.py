from telethon.sessions import StringSession
import telethon
print('telethon', telethon.__version__)
import base64, struct
v = 'w_hSy3LkbG3bCubnfZzmuJ-E0e1jCQE5aLmzOq21WvrcNXyHwyuWlohbqppK3DoOhgh33j1Jl4a-Nfl1CuSRfnOJLnjbRPOIgcD3KF9wrNwYnDdM8f6haTfExCvFZKfzbN6Ir7wWwDKg1mHL6HbfNsDu39yFtvOCqQjIK6bbyBDX-GltQpGYR-4bqfkzSl6EgFFbWSWJliXYwSIHM8TWyXMZCf7OOi9ChkjG7sJbmtfIxsLsvMuxhxYH62OoObRF-90SYz3JiLMzX-EqUhVOliqqkImrkrbiHn5xh2tQi1CS2XPhxOB7563AGEDvC9R4W__qq4i7evC54Zoo='
d = base64.urlsafe_b64decode(v)
print('decoded bytes:', len(d))
dc_id = struct.unpack_from('<i', d, 0)[0]
print('first int (dc_id):', dc_id)
addr_len = d[4]
print('addr len byte:', addr_len)
print('addr:', d[5:5+addr_len])
# try direct parse again with newer style
try:
    s = StringSession(v)
    print('direct parse OK dc:', s.dc_id)
except Exception as e:
    print('direct parse ERR:', e)
