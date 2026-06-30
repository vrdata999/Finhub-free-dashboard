@echo off
REM Install dashboard dependencies. Run once.
REM Usage: install.bat

setlocal

cd /d "%~dp0"

echo === Upgrading pip ===
python -m pip install --upgrade pip --quiet

echo === Installing dashboard requirements (streamlit) ===
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo Install failed. Check the output above.
    exit /b 1
)

echo.
echo === Installed packages ===
python -c "import streamlit, plotly, pandas, requests, dotenv; print('streamlit', streamlit.__version__); print('plotly', plotly.__version__); print('pandas', pandas.__version__); print('requests', requests.__version__); print('python-dotenv OK')"

if errorlevel 1 (
    echo.
    echo Some optional packages are missing - the dashboard will still work,
    echo but please install them with:  python -m pip install plotly pandas requests python-dotenv
)

echo.
echo === Done ===
echo Next: run.bat
endlocal