# Odisha RERA Project & Document Explorer

An automated data extraction tool and interactive dashboard for exploring real estate projects, promoter details, land records, and regulatory filings from the Odisha RERA portal.

## Features
- **Project Exploration & Filter Panel**: Filter projects by District, Tahasil, Start Year, Possession Year, Carpet Area, Project Type, and Status.
- **Dynamic Document Discovery**: Extracts document metadata across Project Overview, Legal, Financial, Land Details, and Promoter filings.
- **HMAC Authentication Handling**: Implements API signing and payload encryption compatible with Odisha RERA backend services.
- **Persistent Token Storage**: Retains manually entered or resolved document access tokens across app reloads.
- **Bulk Export**: Download individual PDFs or bundle all available project documents into an in-memory ZIP archive.

## Quick Start

1. **Clone the repository:**
   \`\`\`bash
   git clone <your-repo-url>
   cd scrapingFilter
   \`\`\`

2. **Set up virtual environment:**
   \`\`\`bash
   python -m venv venv
   # On Windows:
   .\venv\Scripts\Activate.ps1
   \`\`\`

3. **Install dependencies:**
   \`\`\`bash
   pip install -r requirements.txt
   \`\`\`

4. **Run the application:**
   \`\`\`bash
   streamlit run app.py
   \`\`\`
"@
