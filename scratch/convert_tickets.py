import pandas as pd
import os

src_path = "data/customer_support_tickets_200k.csv.bak"
dest_path = "data/nexacorp_tickets.csv"

# Load the first 2000 tickets
df = pd.read_csv(src_path, nrows=2000)

# Map the columns
mapped_df = pd.DataFrame()
mapped_df['ticket_id'] = df['ticket_id'].apply(lambda x: f"TKT-{x}")
mapped_df['employee_name'] = df['customer_name']
mapped_df['issue_description'] = df['issue_description']
mapped_df['status'] = df['status']
mapped_df['exact_error_code'] = ""  # Default empty
mapped_df['created_at'] = df['ticket_created_date']
mapped_df['priority'] = df['priority']
mapped_df['resolution_notes'] = df['resolution_notes']

# Save to destination
mapped_df.to_csv(dest_path, index=False)
print("Successfully converted 2000 tickets!")
