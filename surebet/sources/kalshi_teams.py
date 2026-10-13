"""
Kalshi nombra a los equipos de las ligas de EE. UU. solo por la ciudad ("Brooklyn", "Los Angeles R") y
Polymarket por el apodo ("Nets", "Rams"): sin el nombre completo no se emparejan. Clave: el código del
equipo al final del ticker del mercado (KXNBAGAME-26OCT12BKNWAS-BKN -> BKN).
"""

TEAMS = {
    "KXNBAGAME": {
        "ATL": "Atlanta Hawks", "BKN": "Brooklyn Nets", "BOS": "Boston Celtics", "CHA": "Charlotte Hornets",
        "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers", "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets",
        "DET": "Detroit Pistons", "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
        "LAC": "Los Angeles Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies", "MIA": "Miami Heat",
        "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves", "NOP": "New Orleans Pelicans",
        "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder", "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers",
        "PHX": "Phoenix Suns", "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
        "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
    },
    "KXNFLGAME": {
        "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
        "CAR": "Carolina Panthers", "CHI": "Chicago Bears", "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
        "DAL": "Dallas Cowboys", "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
        "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAC": "Jacksonville Jaguars", "KC": "Kansas City Chiefs",
        "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams", "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins",
        "MIN": "Minnesota Vikings", "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
        "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers", "SEA": "Seattle Seahawks",
        "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers", "TEN": "Tennessee Titans",
        "WAS": "Washington Commanders",
    },
    "KXNHLGAME": {
        "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres", "CAR": "Carolina Hurricanes",
        "CBJ": "Columbus Blue Jackets", "CGY": "Calgary Flames", "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche",
        "DAL": "Dallas Stars", "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers", "FLA": "Florida Panthers",
        "LA": "Los Angeles Kings", "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens", "NJ": "New Jersey Devils",
        "NSH": "Nashville Predators", "NYI": "New York Islanders", "NYR": "New York Rangers", "OTT": "Ottawa Senators",
        "PHI": "Philadelphia Flyers", "PIT": "Pittsburgh Penguins", "SEA": "Seattle Kraken", "SJ": "San Jose Sharks",
        "STL": "St. Louis Blues", "TB": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs", "UTA": "Utah Mammoth",
        "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights", "WPG": "Winnipeg Jets", "WSH": "Washington Capitals",
    },
    "KXMLBGAME": {
        "ATH": "Athletics", "ATL": "Atlanta Braves", "AZ": "Arizona Diamondbacks", "BAL": "Baltimore Orioles",
        "BOS": "Boston Red Sox", "CHC": "Chicago Cubs", "CIN": "Cincinnati Reds", "CLE": "Cleveland Guardians",
        "COL": "Colorado Rockies", "CWS": "Chicago White Sox", "DET": "Detroit Tigers", "HOU": "Houston Astros",
        "KC": "Kansas City Royals", "LAA": "Los Angeles Angels", "LAD": "Los Angeles Dodgers", "MIA": "Miami Marlins",
        "MIL": "Milwaukee Brewers", "MIN": "Minnesota Twins", "NYM": "New York Mets", "NYY": "New York Yankees",
        "PHI": "Philadelphia Phillies", "PIT": "Pittsburgh Pirates", "SD": "San Diego Padres", "SEA": "Seattle Mariners",
        "SF": "San Francisco Giants", "STL": "St. Louis Cardinals", "TB": "Tampa Bay Rays", "TEX": "Texas Rangers",
        "TOR": "Toronto Blue Jays", "WSH": "Washington Nationals",
    },
    "KXWNBAGAME": {
        "ATL": "Atlanta Dream", "CHI": "Chicago Sky", "CONN": "Connecticut Sun", "DAL": "Dallas Wings",
        "GS": "Golden State Valkyries", "IND": "Indiana Fever", "LA": "Los Angeles Sparks", "LV": "Las Vegas Aces",
        "MIN": "Minnesota Lynx", "NY": "New York Liberty", "PDX": "Portland Fire", "PHX": "Phoenix Mercury",
        "SEA": "Seattle Storm", "TOR": "Toronto Tempo", "WSH": "Washington Mystics",
    },
    "KXMLSGAME": {
        "ATL": "Atlanta United", "ATX": "Austin FC", "CHI": "Chicago Fire", "CIN": "FC Cincinnati",
        "CLB": "Columbus Crew", "CLT": "Charlotte FC", "COL": "Colorado Rapids", "DAL": "FC Dallas", "DCU": "DC United",
        "HOU": "Houston Dynamo", "LAFC": "Los Angeles FC", "LAG": "LA Galaxy", "MIA": "Inter Miami",
        "MIN": "Minnesota United", "MTL": "CF Montreal", "NE": "New England Revolution", "NSH": "Nashville SC",
        "NYC": "New York City FC", "NYRB": "New York Red Bulls", "ORL": "Orlando City", "PHI": "Philadelphia Union",
        "POR": "Portland Timbers", "RSL": "Real Salt Lake", "SD": "San Diego FC", "SEA": "Seattle Sounders",
        "SJ": "San Jose Earthquakes", "SKC": "Sporting Kansas City", "STL": "St. Louis City SC", "TOR": "Toronto FC",
        "VAN": "Vancouver Whitecaps",
    },
}
