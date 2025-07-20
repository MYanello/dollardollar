from flask_sqlalchemy import SQLAlchemy

from models.base import Base

# Create extension instances
db = SQLAlchemy(model_class=Base)
