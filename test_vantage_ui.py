import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

def test_vantage_homepage():
    # Initialize Chrome WebDriver (or use headless mode for CI/CD)
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")  # Runs browser in background for CI pipelines
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(options=options)
    
    try:
        # Navigate to application (local server or deployment URL)
        driver.get("http://localhost:5000")
        time.sleep(2)
        
        # Verify page title contains application name
        print(f"Current Page Title: {driver.title}")
        assert "Vantage" in driver.title, "Title validation failed!"
        
        # Check for presence of primary container or heading element
        heading = driver.find_element(By.TAG_NAME, "h1")
        print(f"Found main heading: {heading.text}")
        
        print("TEST PASSED: Vantage UI loaded successfully.")
        
    finally:
        driver.quit()

if __name__ == "__main__":
    test_vantage_homepage()
