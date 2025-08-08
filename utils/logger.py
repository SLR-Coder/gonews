from utils.sheets import log_to_sheet

def error_handler(robot_name, error):
    log_to_sheet(robot_name, "Hata", str(error))
    # Gerekirse işlemi burada durdurabilirsin
    # raise error  # Eğer zinciri tamamen durdurmak istersen
