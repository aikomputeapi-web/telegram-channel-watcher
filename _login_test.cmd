@echo off
rem Test cred-search login with three passwords
curl.exe -s --max-time 20 -X POST "https://cred-search.nowrouter.store/" -d "username=admin&password=Dirty2020!Passwordcameron123" -o NUL -w "NEWPASS: %{http_code} redirect=%{redirect_url}"
echo,
curl.exe -s --max-time 20 -X POST "https://cred-search.nowrouter.store/" -d "username=admin&password=BO49D2LIPH" -o NUL -w "OLDPASS: %{http_code} redirect=%{redirect_url}"
echo,
curl.exe -s --max-time 20 -X POST "https://cred-search.nowrouter.store/" -d "username=admin&password=wrongpass" -o NUL -w "BADCHECK: %{http_code} redirect=%{redirect_url}"
