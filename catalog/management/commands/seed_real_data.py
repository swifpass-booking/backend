"""Populate the catalog with real-world operators, routes and venues for Nepal.

Operators, airports, bus parks, rail stations, flight numbers, route durations and typical fares come
from public timetables/fare ranges. They are NOT a live feed: departure times, fares and seat counts
are approximations, and occurrences are generated on a rolling window from today. Re-running is safe;
existing ids are left untouched and only missing days are added.
"""
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import InventoryZone, Occurrence, Place, Provider, Service

KTM_TZ = ZoneInfo("Asia/Kathmandu")

# id, kind, code, name, city, country, lat, lon
PLACES = [
    # domestic airports
    ("ktm", "airport", "KTM", "Tribhuvan International Airport", "Kathmandu", "NP", 27.6966, 85.3591),
    ("pkr", "airport", "PKR", "Pokhara International Airport", "Pokhara", "NP", 28.1809, 84.0001),
    ("bwa", "airport", "BWA", "Gautam Buddha International Airport", "Bhairahawa", "NP", 27.5056, 83.4160),
    ("bir", "airport", "BIR", "Biratnagar Airport", "Biratnagar", "NP", 26.4815, 87.2640),
    ("bhr", "airport", "BHR", "Bharatpur Airport", "Bharatpur", "NP", 27.6781, 84.4294),
    ("jkr", "airport", "JKR", "Janakpur Airport", "Janakpur", "NP", 26.7088, 85.9224),
    ("npj", "airport", "KEP", "Nepalgunj Airport", "Nepalgunj", "NP", 28.1036, 81.6670),
    ("dhi", "airport", "DHI", "Dhangadhi Airport", "Dhangadhi", "NP", 28.7533, 80.5819),
    ("tmi", "airport", "TMI", "Tumlingtar Airport", "Tumlingtar", "NP", 27.3151, 87.1933),
    ("lua", "airport", "LUA", "Tenzing-Hillary Airport", "Lukla", "NP", 27.6870, 86.7298),
    ("sif", "airport", "SIF", "Simara Airport", "Simara", "NP", 27.1595, 84.9801),
    # international airports
    ("del", "airport", "DEL", "Indira Gandhi International Airport", "Delhi", "IN", 28.5562, 77.1000),
    ("ccu", "airport", "CCU", "Netaji Subhas Chandra Bose International Airport", "Kolkata", "IN", 22.6547, 88.4467),
    ("bom", "airport", "BOM", "Chhatrapati Shivaji Maharaj International Airport", "Mumbai", "IN", 19.0896, 72.8656),
    ("bkk", "airport", "BKK", "Suvarnabhumi Airport", "Bangkok", "TH", 13.6900, 100.7501),
    ("kul", "airport", "KUL", "Kuala Lumpur International Airport", "Kuala Lumpur", "MY", 2.7456, 101.7072),
    ("dxb", "airport", "DXB", "Dubai International Airport", "Dubai", "AE", 25.2532, 55.3657),
    ("shj", "airport", "SHJ", "Sharjah International Airport", "Sharjah", "AE", 25.3286, 55.5172),
    ("doh", "airport", "DOH", "Hamad International Airport", "Doha", "QA", 25.2731, 51.6081),
    ("ist", "airport", "IST", "Istanbul Airport", "Istanbul", "TR", 41.2753, 28.7519),
    ("dac", "airport", "DAC", "Hazrat Shahjalal International Airport", "Dhaka", "BD", 23.8433, 90.3978),
    ("sin", "airport", "SIN", "Changi Airport", "Singapore", "SG", 1.3644, 103.9915),
    ("icn", "airport", "ICN", "Incheon International Airport", "Seoul", "KR", 37.4602, 126.4407),
    # bus parks
    ("ktm_bus", "bus_park", None, "Gongabu New Bus Park", "Kathmandu", "NP", 27.7352, 85.3122),
    ("ktm_kalanki", "bus_park", None, "Kalanki Bus Stop", "Kathmandu", "NP", 27.6933, 85.2810),
    ("pkr_bus", "bus_park", None, "Pokhara Tourist Bus Park", "Pokhara", "NP", 28.2176, 83.9830),
    ("chitwan_bus", "bus_park", None, "Sauraha Bus Stop", "Chitwan", "NP", 27.5782, 84.4994),
    ("narayangadh_bus", "bus_park", None, "Narayangadh Bus Park", "Bharatpur", "NP", 27.6962, 84.4290),
    ("butwal_bus", "bus_park", None, "Butwal Bus Park", "Butwal", "NP", 27.7006, 83.4483),
    ("bhw_bus", "bus_park", None, "Bhairahawa Bus Park", "Bhairahawa", "NP", 27.5000, 83.4500),
    ("lumbini_bus", "bus_park", None, "Lumbini Bus Stop", "Lumbini", "NP", 27.4840, 83.2760),
    ("birgunj_bus", "bus_park", None, "Birgunj Bus Park", "Birgunj", "NP", 27.0104, 84.8770),
    ("janakpur_bus", "bus_park", None, "Janakpur Bus Park", "Janakpur", "NP", 26.7271, 85.9240),
    ("biratnagar_bus", "bus_park", None, "Biratnagar Bus Park", "Biratnagar", "NP", 26.4525, 87.2718),
    ("itahari_bus", "bus_park", None, "Itahari Bus Park", "Itahari", "NP", 26.6630, 87.2718),
    ("dharan_bus", "bus_park", None, "Dharan Bus Park", "Dharan", "NP", 26.8120, 87.2830),
    ("birtamod_bus", "bus_park", None, "Birtamod Bus Park", "Birtamod", "NP", 26.6430, 87.9930),
    ("kakarbhitta_bus", "bus_park", None, "Kakarbhitta Bus Park", "Kakarbhitta", "NP", 26.6510, 88.1610),
    ("nepalgunj_bus", "bus_park", None, "Nepalgunj Bus Park", "Nepalgunj", "NP", 28.0500, 81.6170),
    ("dhangadhi_bus", "bus_park", None, "Dhangadhi Bus Park", "Dhangadhi", "NP", 28.6870, 80.5990),
    ("mahendranagar_bus", "bus_park", None, "Mahendranagar Bus Park", "Mahendranagar", "NP", 28.9640, 80.1790),
    ("hetauda_bus", "bus_park", None, "Hetauda Bus Park", "Hetauda", "NP", 27.4290, 85.0320),
    ("siliguri_bus", "bus_park", None, "Siliguri Tenzing Norgay Bus Terminus", "Siliguri", "IN", 26.7106, 88.4350),
    ("delhi_bus", "bus_park", None, "Anand Vihar ISBT", "Delhi", "IN", 28.6469, 77.3158),
    # rail
    ("jnk_stn", "station", "JNK", "Janakpur Rail Station", "Janakpur", "NP", 26.7288, 85.9266),
    ("jyn_stn", "station", "JYN", "Jayanagar Station", "Jayanagar", "IN", 26.5876, 86.1361),
    ("krt_stn", "station", "KRT", "Kurtha Rail Station", "Kurtha", "NP", 26.6900, 85.9970),
    ("dbg_stn", "station", "DBG", "Darbhanga Junction", "Darbhanga", "IN", 26.1542, 85.8918),
    ("pnbe_stn", "station", "PNBE", "Patna Junction", "Patna", "IN", 25.6000, 85.1380),
    ("ndls_stn", "station", "NDLS", "New Delhi Railway Station", "Delhi", "IN", 28.6428, 77.2197),
    ("howrah_stn", "station", "HWH", "Howrah Junction", "Kolkata", "IN", 22.5839, 88.3426),
    # event venues
    ("dasarath", "venue", None, "Dasarath Rangasala Stadium", "Kathmandu", "NP", 27.6951, 85.3159),
    ("tudikhel", "venue", None, "Tundikhel Ground", "Kathmandu", "NP", 27.7045, 85.3150),
    ("bicc", "venue", None, "Birendra International Convention Centre", "Kathmandu", "NP", 27.6903, 85.3396),
    ("nac", "venue", None, "Nepal Academy Hall", "Kathmandu", "NP", 27.7089, 85.3187),
    ("qfx_civil", "venue", None, "QFX Civil Mall", "Kathmandu", "NP", 27.7016, 85.3122),
    ("qfx_labim", "venue", None, "QFX Labim Mall", "Lalitpur", "NP", 27.6770, 85.3150),
    ("fcube", "venue", None, "FCube Cinemas, Chabahil", "Kathmandu", "NP", 27.7172, 85.3470),
    ("pkr_stadium", "venue", None, "Pokhara Rangasala", "Pokhara", "NP", 28.2087, 83.9856),
    ("lakeside_amp", "venue", None, "Lakeside Amphitheatre", "Pokhara", "NP", 28.2096, 83.9556),
    ("kirtipur_cricket", "venue", None, "TU International Cricket Ground", "Kirtipur", "NP", 27.6796, 85.2889),
    ("mulpani", "venue", None, "Mulpani International Cricket Stadium", "Bhaktapur", "NP", 27.6858, 85.4250),
    ("patan_durbar", "venue", None, "Patan Durbar Square", "Lalitpur", "NP", 27.6727, 85.3253),
    ("bsl_stadium", "venue", None, "Biratnagar Sports Complex", "Biratnagar", "NP", 26.4560, 87.2710),
]

# id, legal/display name, modes, adapter
PROVIDERS = [
    ("buddha", "Buddha Air", ["air"], "ndc_v21"),
    ("yeti", "Yeti Airlines", ["air"], "ndc_v21"),
    ("shree", "Shree Airlines", ["air"], "ndc_v21"),
    ("sita", "Sita Air", ["air"], "ndc_v21"),
    ("summit", "Summit Air", ["air"], "ndc_v21"),
    ("tara", "Tara Air", ["air"], "ndc_v21"),
    ("saurya", "Saurya Airlines", ["air"], "ndc_v21"),
    ("nepal_air", "Nepal Airlines", ["air"], "ndc_v21"),
    ("himalaya", "Himalaya Airlines", ["air"], "ndc_v21"),
    ("airindia", "Air India", ["air"], "ndc_v21"),
    ("indigo", "IndiGo", ["air"], "ndc_v21"),
    ("thai", "Thai Airways", ["air"], "ndc_v21"),
    ("malaysia", "Malaysia Airlines", ["air"], "ndc_v21"),
    ("batik", "Batik Air Malaysia", ["air"], "ndc_v21"),
    ("flydubai", "flydubai", ["air"], "ndc_v21"),
    ("airarabia", "Air Arabia", ["air"], "ndc_v21"),
    ("qatar", "Qatar Airways", ["air"], "ndc_v21"),
    ("turkish", "Turkish Airlines", ["air"], "ndc_v21"),
    ("biman", "Biman Bangladesh Airlines", ["air"], "ndc_v21"),
    ("scoot", "Scoot", ["air"], "ndc_v21"),
    ("greenline", "Greenline Tours", ["bus"], "native_console"),
    ("sajha", "Sajha Yatayat", ["bus"], "native_console"),
    ("mountain_overland", "Mountain Overland Express", ["bus"], "native_console"),
    ("tourist_coach", "Tourist Bus Service Pokhara", ["bus"], "native_console"),
    ("nepal_yatayat", "Nepal Yatayat Sanstha", ["bus"], "native_console"),
    ("siddhartha", "Siddhartha Deluxe", ["bus"], "native_console"),
    ("janakpur_exp", "Janakpur Express Deluxe", ["bus"], "native_console"),
    ("kankai", "Kankai Express", ["bus"], "native_console"),
    ("sudur", "Sudurpaschim Deluxe", ["bus"], "native_console"),
    ("intl_coach", "Nepal-India Cross Border Coach", ["bus"], "native_console"),
    ("nepal_railway", "Nepal Railway Company", ["rail"], "csv_timetable"),
    ("indian_rail", "Indian Railways", ["rail"], "csv_timetable"),
    ("lakeside_live", "Lakeside Live", ["event"], "native_console"),
    ("qfx", "QFX Cinemas", ["event"], "native_console"),
    ("fcube", "FCube Cinemas", ["event"], "native_console"),
    ("anfa", "All Nepal Football Association", ["event"], "native_console"),
    ("cricket_nepal", "Cricket Association of Nepal", ["event"], "native_console"),
    ("kathmandu_events", "Kathmandu Live Events", ["event"], "native_console"),
]

# Domestic flights: (provider, code, from, to, duration_min, base_npr, departure times, aircraft)
DOMESTIC = [
    ("buddha", "U4-601", "ktm", "pkr", 25, 6200, ["06:30", "08:15", "10:45", "13:30", "16:00"], "ATR 72-500"),
    ("yeti", "YT-681", "ktm", "pkr", 25, 6000, ["07:00", "09:30", "12:15", "15:45"], "ATR 72-500"),
    ("shree", "11-203", "ktm", "pkr", 25, 5800, ["07:30", "11:00", "14:30"], "CRJ-200"),
    ("saurya", "SL-541", "ktm", "pkr", 25, 5900, ["08:00", "12:45"], "CRJ-200"),
    ("buddha", "U4-611", "pkr", "ktm", 25, 6200, ["07:15", "09:00", "11:30", "14:15", "16:45"], "ATR 72-500"),
    ("yeti", "YT-684", "pkr", "ktm", 25, 6000, ["08:00", "10:30", "13:15", "16:30"], "ATR 72-500"),
    ("buddha", "U4-801", "ktm", "bwa", 30, 7400, ["07:00", "11:00", "15:30"], "ATR 72-500"),
    ("yeti", "YT-691", "ktm", "bwa", 30, 7600, ["08:30", "13:00"], "ATR 72-500"),
    ("shree", "11-211", "ktm", "bwa", 30, 7200, ["09:30"], "CRJ-200"),
    ("buddha", "U4-811", "bwa", "ktm", 30, 7400, ["08:00", "12:30", "16:45"], "ATR 72-500"),
    ("buddha", "U4-501", "ktm", "bir", 45, 8900, ["06:45", "09:15", "12:00", "15:15"], "ATR 72-500"),
    ("yeti", "YT-701", "ktm", "bir", 45, 9100, ["07:30", "10:30", "14:00"], "ATR 72-500"),
    ("shree", "11-401", "ktm", "bir", 45, 8700, ["08:15", "13:45"], "CRJ-200"),
    ("saurya", "SL-601", "ktm", "bir", 45, 8800, ["09:00"], "CRJ-200"),
    ("buddha", "U4-511", "bir", "ktm", 45, 8900, ["07:45", "10:15", "13:00", "16:15"], "ATR 72-500"),
    ("yeti", "YT-711", "bir", "ktm", 45, 9100, ["08:30", "11:30", "15:00"], "ATR 72-500"),
    ("buddha", "U4-701", "ktm", "bhr", 20, 5200, ["07:00", "10:00", "14:00"], "ATR 72-500"),
    ("buddha", "U4-711", "bhr", "ktm", 20, 5200, ["07:40", "10:40", "14:40"], "ATR 72-500"),
    ("buddha", "U4-301", "ktm", "jkr", 35, 7900, ["08:00", "12:00"], "ATR 72-500"),
    ("yeti", "YT-731", "ktm", "jkr", 35, 8100, ["09:30"], "ATR 72-500"),
    ("buddha", "U4-311", "jkr", "ktm", 35, 7900, ["09:00", "13:00"], "ATR 72-500"),
    ("buddha", "U4-901", "ktm", "npj", 55, 9800, ["07:30", "12:00", "15:00"], "ATR 72-500"),
    ("yeti", "YT-801", "ktm", "npj", 55, 10100, ["09:00", "13:30"], "ATR 72-500"),
    ("shree", "11-501", "ktm", "npj", 55, 9500, ["08:30"], "CRJ-200"),
    ("buddha", "U4-911", "npj", "ktm", 55, 9800, ["09:00", "13:30", "16:30"], "ATR 72-500"),
    ("buddha", "U4-951", "ktm", "dhi", 65, 11200, ["07:00", "11:30"], "ATR 72-500"),
    ("yeti", "YT-851", "ktm", "dhi", 65, 11500, ["08:30"], "ATR 72-500"),
    ("buddha", "U4-961", "dhi", "ktm", 65, 11200, ["09:00", "13:30"], "ATR 72-500"),
    ("yeti", "YT-761", "ktm", "tmi", 40, 8300, ["07:30"], "ATR 72-500"),
    ("sita", "SI-101", "ktm", "lua", 35, 15000, ["06:15", "07:00", "07:45"], "Dornier 228"),
    ("summit", "S5-201", "ktm", "lua", 35, 15500, ["06:30", "07:30"], "Dornier 228"),
    ("tara", "TB-301", "ktm", "lua", 35, 15200, ["06:45", "07:15"], "Dornier 228"),
    ("sita", "SI-102", "lua", "ktm", 35, 15000, ["07:30", "08:15", "09:00"], "Dornier 228"),
    ("summit", "S5-202", "lua", "ktm", 35, 15500, ["08:00", "09:30"], "Dornier 228"),
    ("nepal_air", "RA-101", "ktm", "bir", 45, 8400, ["10:00"], "Twin Otter"),
    ("nepal_air", "RA-201", "ktm", "sif", 25, 6500, ["11:00"], "Twin Otter"),
    ("shree", "11-601", "pkr", "bwa", 25, 5600, ["10:00"], "CRJ-200"),
]

# International flights:
# (provider, code, from, to, duration_min, base_npr, depart, cabin_choices, aircraft, daily?)
INTL = [
    ("airindia", "AI-214", "ktm", "del", 135, 22000, ["09:45", "17:50"], "Airbus A320neo"),
    ("indigo", "6E-1111", "ktm", "del", 140, 20500, ["06:30", "14:15"], "Airbus A320neo"),
    ("airindia", "AI-215", "del", "ktm", 135, 21500, ["12:30", "20:30"], "Airbus A320neo"),
    ("indigo", "6E-1112", "del", "ktm", 140, 20000, ["09:00", "17:30"], "Airbus A320neo"),
    ("airindia", "AI-231", "ktm", "ccu", 105, 19500, ["13:00"], "Airbus A320"),
    ("airindia", "AI-232", "ccu", "ktm", 105, 19000, ["15:30"], "Airbus A320"),
    ("indigo", "6E-2401", "ktm", "bom", 190, 26500, ["10:30"], "Airbus A320neo"),
    ("thai", "TG-320", "ktm", "bkk", 215, 29500, ["11:20"], "Airbus A320"),
    ("thai", "TG-319", "bkk", "ktm", 215, 29000, ["17:35"], "Airbus A320"),
    ("malaysia", "MH-211", "ktm", "kul", 280, 32000, ["07:30"], "Boeing 737-800"),
    ("batik", "OD-170", "ktm", "kul", 285, 29000, ["22:15"], "Boeing 737-800"),
    ("batik", "OD-171", "kul", "ktm", 290, 28500, ["13:00"], "Boeing 737-800"),
    ("flydubai", "FZ-576", "ktm", "dxb", 270, 38500, ["06:30", "23:45"], "Boeing 737 MAX 8"),
    ("flydubai", "FZ-577", "dxb", "ktm", 270, 38000, ["03:40", "11:00"], "Boeing 737 MAX 8"),
    ("airarabia", "G9-505", "ktm", "shj", 270, 33500, ["20:30"], "Airbus A320"),
    ("airarabia", "G9-506", "shj", "ktm", 270, 33000, ["10:30"], "Airbus A320"),
    ("qatar", "QR-649", "ktm", "doh", 295, 46000, ["02:15", "20:50"], "Airbus A320"),
    ("qatar", "QR-650", "doh", "ktm", 295, 45500, ["09:25", "14:10"], "Airbus A320"),
    ("turkish", "TK-726", "ktm", "ist", 440, 71000, ["22:40"], "Airbus A330-300"),
    ("turkish", "TK-727", "ist", "ktm", 440, 70000, ["12:50"], "Airbus A330-300"),
    ("biman", "BG-371", "ktm", "dac", 105, 24500, ["12:00"], "Boeing 737-800"),
    ("biman", "BG-372", "dac", "ktm", 105, 24000, ["15:30"], "Boeing 737-800"),
    ("himalaya", "H9-701", "ktm", "kul", 280, 30500, ["23:00"], "Airbus A320"),
    ("himalaya", "H9-801", "ktm", "dxb", 270, 36500, ["07:40"], "Airbus A320"),
    ("himalaya", "H9-901", "ktm", "doh", 295, 44000, ["09:00"], "Airbus A320"),
    ("nepal_air", "RA-409", "ktm", "kul", 280, 29500, ["13:30"], "Airbus A330-200"),
    ("nepal_air", "RA-205", "ktm", "del", 135, 20000, ["08:20"], "Airbus A320"),
    ("scoot", "TR-161", "sin", "ktm", 330, 38000, ["22:30"], "Boeing 787-8"),
    ("scoot", "TR-162", "ktm", "sin", 330, 38500, ["03:30"], "Boeing 787-8"),
]

# Buses: (provider, code, from, to, duration_min, fare_npr, depart times, coach, amenities, capacity)
BUSES = [
    ("greenline", "GL-KP", "ktm_bus", "pkr_bus", 420, 2300, ["07:00"], "luxury", ["wifi", "charging", "water", "snacks"], 28),
    ("tourist_coach", "TC-KP", "ktm_kalanki", "pkr_bus", 450, 1100, ["07:00", "07:30", "08:00"], "tourist", ["charging"], 36),
    ("sajha", "SY-KP", "ktm_bus", "pkr_bus", 480, 1000, ["06:30", "20:00"], "deluxe", ["water"], 40),
    ("siddhartha", "SD-KP", "ktm_bus", "pkr_bus", 450, 1400, ["07:30", "21:00"], "deluxe", ["charging", "water"], 40),
    ("tourist_coach", "TC-PK", "pkr_bus", "ktm_kalanki", 450, 1100, ["07:00", "07:30", "08:00"], "tourist", ["charging"], 36),
    ("greenline", "GL-PK", "pkr_bus", "ktm_bus", 420, 2300, ["07:00"], "luxury", ["wifi", "charging", "water", "snacks"], 28),
    ("greenline", "GL-KS", "ktm_bus", "chitwan_bus", 330, 1800, ["07:30"], "luxury", ["wifi", "charging", "water"], 28),
    ("tourist_coach", "TC-KS", "ktm_kalanki", "chitwan_bus", 360, 900, ["07:30", "08:00"], "tourist", ["charging"], 36),
    ("sajha", "SY-KN", "ktm_bus", "narayangadh_bus", 300, 700, ["06:00", "09:00", "13:00"], "deluxe", ["water"], 40),
    ("greenline", "GL-PS", "pkr_bus", "chitwan_bus", 300, 1800, ["07:30"], "luxury", ["wifi", "charging", "water"], 28),
    ("tourist_coach", "TC-PS", "pkr_bus", "chitwan_bus", 330, 900, ["07:45"], "tourist", ["charging"], 36),
    ("siddhartha", "SD-KL", "ktm_bus", "bhw_bus", 600, 1600, ["06:30", "18:30"], "deluxe", ["charging", "water"], 40),
    ("sajha", "SY-KB", "ktm_bus", "butwal_bus", 540, 1300, ["06:00", "19:00"], "ac", ["water"], 40),
    ("tourist_coach", "TC-PL", "pkr_bus", "lumbini_bus", 420, 1100, ["07:00"], "tourist", ["charging"], 36),
    ("tourist_coach", "TC-LP", "lumbini_bus", "pkr_bus", 420, 1100, ["07:00"], "tourist", ["charging"], 36),
    ("siddhartha", "SD-PB", "pkr_bus", "butwal_bus", 300, 700, ["06:30", "10:30"], "deluxe", ["water"], 40),
    ("siddhartha", "SD-BP", "butwal_bus", "pkr_bus", 300, 700, ["06:30", "10:30"], "deluxe", ["water"], 40),
    ("janakpur_exp", "JE-KJ", "ktm_bus", "janakpur_bus", 540, 1200, ["06:00", "19:00"], "deluxe", ["water", "charging"], 40),
    ("janakpur_exp", "JE-JK", "janakpur_bus", "ktm_bus", 540, 1200, ["06:00", "19:00"], "deluxe", ["water", "charging"], 40),
    ("nepal_yatayat", "NY-KB", "ktm_bus", "birgunj_bus", 360, 800, ["06:00", "08:00", "13:00"], "ac", ["water"], 40),
    ("nepal_yatayat", "NY-BK", "birgunj_bus", "ktm_bus", 360, 800, ["06:00", "08:00", "13:00"], "ac", ["water"], 40),
    ("kankai", "KK-KB", "ktm_bus", "biratnagar_bus", 720, 1900, ["15:00", "17:30"], "deluxe", ["charging", "water", "blanket"], 36),
    ("kankai", "KK-BK", "biratnagar_bus", "ktm_bus", 720, 1900, ["15:00", "17:30"], "deluxe", ["charging", "water", "blanket"], 36),
    ("kankai", "KK-KK", "ktm_bus", "kakarbhitta_bus", 840, 2200, ["14:00", "16:00"], "deluxe", ["charging", "water", "blanket"], 36),
    ("kankai", "KK-KD", "ktm_bus", "dharan_bus", 780, 2000, ["15:30"], "deluxe", ["charging", "water", "blanket"], 36),
    ("kankai", "KK-IT", "ktm_bus", "itahari_bus", 720, 1800, ["16:00"], "deluxe", ["charging", "water"], 36),
    ("kankai", "KK-BT", "ktm_bus", "birtamod_bus", 810, 2100, ["15:30"], "deluxe", ["charging", "water"], 36),
    ("sudur", "SP-KN", "ktm_bus", "nepalgunj_bus", 840, 2300, ["13:00", "16:00"], "deluxe", ["charging", "water", "blanket"], 36),
    ("sudur", "SP-NK", "nepalgunj_bus", "ktm_bus", 840, 2300, ["13:00", "16:00"], "deluxe", ["charging", "water", "blanket"], 36),
    ("sudur", "SP-KD", "ktm_bus", "dhangadhi_bus", 1020, 2900, ["12:00"], "deluxe", ["charging", "water", "blanket"], 36),
    ("sudur", "SP-KM", "ktm_bus", "mahendranagar_bus", 1080, 3000, ["11:30"], "deluxe", ["charging", "water", "blanket"], 36),
    ("sajha", "SY-KH", "ktm_kalanki", "hetauda_bus", 240, 450, ["06:30", "10:00", "14:00"], "ac", ["water"], 40),
    ("intl_coach", "NI-KD", "ktm_bus", "delhi_bus", 1740, 5500, ["06:00"], "sleeper", ["charging", "blanket", "water"], 30),
    ("intl_coach", "NI-DK", "delhi_bus", "ktm_bus", 1740, 5500, ["06:00"], "sleeper", ["charging", "blanket", "water"], 30),
    ("intl_coach", "NI-KS", "ktm_bus", "siliguri_bus", 1080, 3500, ["07:00"], "deluxe", ["charging", "water"], 36),
    ("intl_coach", "NI-SK", "siliguri_bus", "ktm_bus", 1080, 3500, ["07:00"], "deluxe", ["charging", "water"], 36),
]

# Trains: (provider, code, number, from, to, duration_min, fare_npr, times, class, capacity)
TRAINS = [
    ("nepal_railway", "NR-01", "NR-01", "jnk_stn", "jyn_stn", 60, 85, ["09:30"], "second", 220),
    ("nepal_railway", "NR-02", "NR-02", "jyn_stn", "jnk_stn", 60, 85, ["13:00"], "second", 220),
    ("nepal_railway", "NR-03", "NR-03", "jnk_stn", "krt_stn", 40, 60, ["16:00"], "second", 220),
    ("nepal_railway", "NR-04", "NR-04", "krt_stn", "jnk_stn", 40, 60, ["07:00"], "second", 220),
    ("nepal_railway", "NR-05", "NR-05", "jyn_stn", "jnk_stn", 60, 85, ["07:45"], "second", 220),
    ("indian_rail", "12557", "12557", "jyn_stn", "ndls_stn", 1260, 1900, ["17:30"], "sleeper", 720),
    ("indian_rail", "12558", "12558", "ndls_stn", "jyn_stn", 1260, 1900, ["19:20"], "sleeper", 720),
    ("indian_rail", "13022", "13022", "jyn_stn", "howrah_stn", 960, 1400, ["20:10"], "sleeper", 720),
    ("indian_rail", "15548", "15548", "jyn_stn", "pnbe_stn", 480, 900, ["10:15"], "second", 600),
    ("indian_rail", "15549", "15549", "pnbe_stn", "jyn_stn", 480, 900, ["14:00"], "second", 600),
    ("indian_rail", "15283", "15283", "jyn_stn", "dbg_stn", 90, 350, ["06:30"], "second", 600),
]

# Events: (provider, title, venue, category, language, age, day offsets, time, duration_min, tiers[(code,label,npr,cap)])
EVENTS = [
    ("qfx", "Now Showing — Nepali Feature, Hall 1", "qfx_civil", "cinema", "ne", "PG-13", range(0, 30), ["12:30", "15:30", "18:30"], 150, [("STD", "Standard", 450, 120), ("GOLD", "Gold Class", 900, 30)]),
    ("qfx", "Now Showing — Hollywood Release, Hall 2", "qfx_civil", "cinema", "en", "PG-13", range(0, 30), ["13:00", "16:30", "20:00"], 140, [("STD", "Standard", 500, 120), ("GOLD", "Gold Class", 1000, 30)]),
    ("qfx", "Now Showing — Hindi Feature", "qfx_labim", "cinema", "hi", "PG-13", range(0, 30), ["14:00", "17:30", "21:00"], 150, [("STD", "Standard", 450, 110)]),
    ("fcube", "Now Showing — Nepali Feature", "fcube", "cinema", "ne", "PG", range(0, 30), ["12:00", "15:00", "18:00"], 145, [("STD", "Standard", 400, 100)]),
    ("lakeside_live", "Sur Sudha — Lakeside Evening", "lakeside_amp", "concert", "ne", None, range(0, 30, 3), ["18:30"], 150, [("GA", "General Admission", 800, 400), ("VIP", "VIP", 2500, 60)]),
    ("lakeside_live", "Himalayan Folk Night", "lakeside_amp", "concert", "ne", None, range(2, 30, 7), ["19:00"], 135, [("GA", "General Admission", 600, 300), ("VIP", "VIP", 2000, 50)]),
    ("kathmandu_events", "Kathmandu Jazz Night", "nac", "concert", "en", None, range(4, 60, 14), ["18:00"], 180, [("GA", "General Admission", 1500, 500), ("VIP", "VIP", 4000, 80)]),
    ("kathmandu_events", "Nepali Classical Music Evening", "nac", "concert", "ne", None, range(6, 60, 14), ["17:30"], 150, [("GA", "General Admission", 500, 300)]),
    ("kathmandu_events", "Tech Summit Nepal", "bicc", "conference", "en", None, [20, 21], ["09:00"], 480, [("STD", "Standard Pass", 3000, 800), ("VIP", "VIP Pass", 8000, 100)]),
    ("kathmandu_events", "Tihar Deusi Bhailo Cultural Show", "tudikhel", "festival", "ne", None, range(26, 33), ["16:00"], 240, [("GA", "General Admission", 300, 2000)]),
    ("kathmandu_events", "Dashain Cultural Fair", "tudikhel", "festival", "ne", None, range(5, 16), ["11:00"], 420, [("GA", "General Admission", 200, 3000)]),
    ("kathmandu_events", "Patan Heritage Walk and Newari Food Festival", "patan_durbar", "festival", "en", None, range(1, 30, 5), ["10:00"], 240, [("GA", "General Admission", 700, 150)]),
    ("anfa", "Martyr's Memorial A-Division League", "dasarath", "sports", "ne", None, range(3, 45, 7), ["15:00"], 120, [("STAND", "Stand", 300, 8000), ("VIP", "VIP Stand", 1000, 600)]),
    ("anfa", "Nepal National Football Team — International Friendly", "dasarath", "sports", "ne", None, [28], ["16:00"], 120, [("STAND", "Stand", 500, 10000), ("VIP", "VIP Stand", 2000, 800)]),
    ("anfa", "Pokhara Premier Football Match", "pkr_stadium", "sports", "ne", None, range(5, 40, 10), ["15:00"], 120, [("STAND", "Stand", 250, 4000)]),
    ("cricket_nepal", "Nepal Premier League T20", "kirtipur_cricket", "sports", "en", None, range(10, 25), ["14:00"], 240, [("STAND", "General Stand", 500, 12000), ("PAV", "Pavilion", 2500, 1200)]),
    ("cricket_nepal", "Nepal vs Netherlands — ODI", "mulpani", "sports", "en", None, [18], ["09:30"], 480, [("STAND", "General Stand", 800, 10000), ("PAV", "Pavilion", 3500, 1000)]),
    ("cricket_nepal", "Biratnagar Super League Match", "bsl_stadium", "sports", "ne", None, range(8, 30, 6), ["14:00"], 240, [("STAND", "General Stand", 300, 5000)]),
]

ALL_PLACE_COUNTRIES_TZ = {
    "NP": "Asia/Kathmandu", "IN": "Asia/Kolkata", "TH": "Asia/Bangkok", "MY": "Asia/Kuala_Lumpur",
    "AE": "Asia/Dubai", "QA": "Asia/Qatar", "TR": "Europe/Istanbul", "BD": "Asia/Dhaka",
    "SG": "Asia/Singapore", "KR": "Asia/Seoul",
}


def _utc(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    local = datetime(day.year, day.month, day.day, h, m, tzinfo=KTM_TZ)
    return local.astimezone(ZoneInfo("UTC"))


class Command(BaseCommand):
    help = "Seed real Nepal-centric operators, routes, fares and events (flights, buses, trains, events)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="How many days ahead to generate.")
        parser.add_argument("--seed", type=int, default=7)

    def handle(self, *args, **opts):
        self.rng = random.Random(opts["seed"])
        self.days = opts["days"]
        today = timezone.now().astimezone(KTM_TZ).date()
        self.dates = [today + timedelta(days=i) for i in range(self.days)]
        self.today = today

        self._places()
        self._providers()
        self._flights(DOMESTIC, "dom")
        self._flights(INTL, "intl")
        self._buses()
        self._trains()
        self._events()

        from django.db.models import Count
        counts = dict(Occurrence.objects.values_list("mode").annotate(c=Count("id")))
        self.stdout.write(self.style.SUCCESS(
            f"Catalog now has {Place.objects.count()} places, {Provider.objects.count()} providers, "
            f"{Service.objects.count()} services, {Occurrence.objects.count()} occurrences "
            f"{counts}, {InventoryZone.objects.count()} zones."
        ))

    # ---- helpers -------------------------------------------------------
    def _places(self):
        for pid, kind, code, name, city, cc, lat, lon in PLACES:
            Place.objects.update_or_create(
                id=f"plc_{pid}",
                defaults=dict(kind=kind, code=code, name=name, city=city, country=cc,
                              timezone=ALL_PLACE_COUNTRIES_TZ[cc], latitude=lat, longitude=lon),
            )

    def _providers(self):
        for pid, name, modes, adapter in PROVIDERS:
            Provider.objects.update_or_create(
                id=f"prv_{pid}",
                defaults=dict(legal_name=name, display_name=name, modes=modes, adapter_key=adapter, is_self_serve=False),
            )

    def _service(self, sid, provider, mode, title, code=None, origin=None, dest=None, venue=None, attrs=None):
        svc, _ = Service.objects.update_or_create(
            id=sid,
            defaults=dict(
                provider_id=f"prv_{provider}", mode=mode, code=code, title=title,
                origin_place_id=f"plc_{origin}" if origin else None,
                dest_place_id=f"plc_{dest}" if dest else None,
                venue_place_id=f"plc_{venue}" if venue else None,
                attributes=attrs or {},
            ),
        )
        return svc

    def _emit(self, svc, mode, departs, duration, price_npr, capacity, zones, gates_before=None):
        """Create one occurrence + its zones if it does not exist yet. zones: [(code,label,npr,cap,reserved)]."""
        occ_id = f"occ_{svc.id[4:]}_{departs:%Y%m%d%H%M}"[:32]
        if Occurrence.objects.filter(id=occ_id).exists():
            return
        if departs <= timezone.now():
            return
        sold = int(capacity * self.rng.uniform(0.05, 0.75))
        occ = Occurrence.objects.create(
            id=occ_id, service=svc, mode=mode, departs_at=departs,
            arrives_at=departs + timedelta(minutes=duration),
            gates_open_at=departs - timedelta(minutes=gates_before) if gates_before else None,
            status="scheduled", base_price_minor=int(price_npr * 100), capacity=capacity,
            seats_sold=sold, provider_ref=f"{svc.code or svc.id}-{departs:%Y%m%d}",
        )
        InventoryZone.objects.bulk_create([
            InventoryZone(id=f"z{occ_id[3:][:30 - len(code)]}_{code}", occurrence=occ, code=code, label=label,
                          price_minor=int(npr * 100), capacity=cap, is_reserved_seating=reserved)
            for code, label, npr, cap, reserved in zones
        ])

    def _price(self, base, day, depart_hhmm):
        """Weekend/peak and last-minute-to-far-out shape; deterministic enough via the seeded rng."""
        mult = 1.0
        if day.weekday() in (4, 6):  # Fri, Sun
            mult += 0.08
        hour = int(depart_hhmm.split(":")[0])
        if hour < 9 or 16 <= hour < 19:
            mult += 0.04
        mult += self.rng.uniform(-0.04, 0.06)
        return round(base * mult / 50) * 50

    # ---- modes ---------------------------------------------------------
    def _flights(self, table, scope):
        for provider, code, frm, to, dur, base, times, aircraft in table:
            f, t = Place.objects.get(id=f"plc_{frm}"), Place.objects.get(id=f"plc_{to}")
            svc = self._service(
                f"svc_{scope}_{code.replace('-', '').lower()}"[:32], provider, "air",
                f"{f.city} → {t.city}", code=code, origin=frm, dest=to,
                attrs={"flightNumber": code, "aircraft": aircraft, "cabin": "economy",
                       "baggageKg": 30 if scope == "intl" else 15, "stops": 0,
                       "international": scope == "intl"},
            )
            cap = 68 if scope == "dom" and "ATR" in aircraft else (19 if "Dornier" in aircraft or "Twin Otter" in aircraft else 180 if scope == "intl" else 50)
            for day in self.dates:
                for hhmm in times:
                    price = self._price(base, day, hhmm)
                    zones = [("ECON", "Economy", price, cap, True)]
                    if scope == "intl" and cap >= 150:
                        zones.append(("BIZ", "Business", round(price * 2.8 / 100) * 100, 12, True))
                    self._emit(svc, "air", _utc(day, hhmm), dur, price, cap + (12 if len(zones) > 1 else 0), zones,
                               gates_before=120 if scope == "intl" else 60)

    def _buses(self):
        for provider, code, frm, to, dur, base, times, coach, amenities, cap in BUSES:
            f, t = Place.objects.get(id=f"plc_{frm}"), Place.objects.get(id=f"plc_{to}")
            svc = self._service(
                f"svc_bus_{code.replace('-', '').lower()}", provider, "bus", f"{f.city} → {t.city}", code=code,
                origin=frm, dest=to,
                attrs={"coachType": coach, "hasAc": coach in ("luxury", "ac", "sleeper", "tourist"),
                       "amenities": amenities, "boardingPoint": f.name, "droppingPoint": t.name},
            )
            label = {"luxury": "Luxury A/C", "ac": "A/C", "deluxe": "Deluxe", "tourist": "Tourist", "sleeper": "Sleeper"}[coach]
            for day in self.dates:
                for hhmm in times:
                    price = self._price(base, day, hhmm)
                    self._emit(svc, "bus", _utc(day, hhmm), dur, price, cap, [("DELUXE", label, price, cap, True)],
                               gates_before=20)

    def _trains(self):
        for provider, code, number, frm, to, dur, base, times, klass, cap in TRAINS:
            f, t = Place.objects.get(id=f"plc_{frm}"), Place.objects.get(id=f"plc_{to}")
            svc = self._service(
                f"svc_rail_{code.lower()}", provider, "rail", f"{f.city} → {t.city}", code=code, origin=frm, dest=to,
                attrs={"trainNumber": number, "coachClass": klass, "platform": None},
            )
            label = {"second": "Second Class", "sleeper": "Sleeper"}[klass]
            for day in self.dates:
                for hhmm in times:
                    price = round(base * (1 + self.rng.uniform(-0.01, 0.01)))
                    self._emit(svc, "rail", _utc(day, hhmm), dur, price, cap, [(klass.upper()[:6], label, price, cap, True)],
                               gates_before=30)

    def _events(self):
        for i, (provider, title, venue, cat, lang, age, offsets, times, dur, tiers) in enumerate(EVENTS):
            svc = self._service(
                f"svc_evt_{i:02d}", provider, "event", title, venue=venue,
                attrs={"category": cat, "language": lang, "ageRating": age},
            )
            total = sum(c for *_, c in tiers)
            for off in offsets:
                if off >= self.days + 30:
                    continue
                day = self.today + timedelta(days=off)
                for hhmm in times:
                    zones = [(code, label, round(npr * (1 + self.rng.uniform(-0.02, 0.02))), cap, True)
                             for code, label, npr, cap in tiers]
                    self._emit(svc, "event", _utc(day, hhmm), dur, zones[0][2], total, zones, gates_before=45)
