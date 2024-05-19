import subprocess
import sys
import tkinter as tk
from tkinter import filedialog
import os
import ollama
from openai import OpenAI
import argparse
import json
import re
import shutil

# ANSI escape codes for colors
PINK = '\033[95m'
CYAN = '\033[96m'
YELLOW = '\033[93m'
NEON_GREEN = '\033[92m'
RESET_COLOR = '\033[0m'

def rewrite_query(user_input_json, conversation_history, ollama_model):
    user_input = json.loads(user_input_json)["Query"]
    context = "\n".join([f"{msg['role']}: {msg['content']}" for msg in conversation_history[-2:]])
    prompt = f"""Rewrite the following query by incorporating relevant context from the conversation history.
    Return ONLY the rewritten query text, without any additional formatting or explanations.
    
    Conversation History:
    {context}
    
    Original query: [{user_input}]
    
    Rewritten query: 
    """
    response = client.chat.completions.create(
        model=ollama_model,
        messages=[{"role": "system", "content": prompt}],
        max_tokens=200,
        n=1,
        temperature=0.01,
    )
    rewritten_query = response.choices[0].message.content.strip()
    return json.dumps({"Rewritten Query": rewritten_query})

def ollama_chat(user_input, system_message, ollama_model, conversation_history, output_file):
    conversation_history.append({"role": "user", "content": user_input})

    if len(conversation_history) > 1:
        query_json = {
            "Query": user_input,
            "Rewritten Query": ""
        }
        rewritten_query_json = rewrite_query(json.dumps(query_json), conversation_history, ollama_model)
        rewritten_query_data = json.loads(rewritten_query_json)
        rewritten_query = rewritten_query_data["Rewritten Query"]
        print(PINK + "Original Query: " + user_input + RESET_COLOR)
        print(PINK + "Rewritten Query: " + rewritten_query + RESET_COLOR)
    else:
        rewritten_query = user_input

    messages = [
        {"role": "system", "content": system_message},
        *conversation_history
    ]

    response = client.chat.completions.create(
        model=ollama_model,
        messages=messages,
        max_tokens=200,
        stream=True  # Enable streaming
    )

    # Stream the response
    collected_messages = []
    for chunk in response:
        chunk_message = chunk.choices[0].delta.content
        if chunk_message:
            collected_messages.append(chunk_message)
            print(NEON_GREEN + chunk_message + RESET_COLOR, end='', flush=True)

            # Write the chunk to the output file
            with open(output_file, 'a') as file:
                file.write(chunk_message)

    full_response = ''.join(collected_messages)
    conversation_history.append({"role": "assistant", "content": full_response})

    return full_response

def backup_file(script_path, project_folder, bug_fix_counter):
    backup_folder = os.path.join(project_folder, f"{os.path.basename(project_folder)}BACKUPS", f"{bug_fix_counter}-{bug_fix_counter}")
    os.makedirs(backup_folder, exist_ok=True)

    # Backup the current working copy of the script
    backup_file_path = os.path.join(backup_folder, f"{os.path.basename(project_folder)}CURRCOPY{os.path.splitext(script_path)[1]}")
    shutil.copy2(script_path, backup_file_path)
    print(f"Backed up the current working copy to {backup_file_path}")

    # Backup the current working environment
    backup_env_folder = os.path.join(backup_folder, f"{os.path.basename(project_folder)}CURRENV")
    os.makedirs(backup_env_folder, exist_ok=True)
    for file_name in os.listdir(os.path.dirname(script_path)):
        file_path = os.path.join(os.path.dirname(script_path), file_name)
        if os.path.isfile(file_path):
            shutil.copy2(file_path, os.path.join(backup_env_folder, file_name))
    print(f"Backed up the current working environment to {backup_env_folder}")

def run_script(script_path, project_name, bug_fix_counter):
    project_folder = os.path.join("Projects", project_name)
    
    # Create backups of the current working copy and environment
    backup_file(script_path, project_folder, bug_fix_counter)    

    try:
        # Run the script using subprocess
        result = subprocess.run(['python3', script_path], capture_output=True, text=True)

        # Check if the script executed successfully
        if result.returncode == 0:
            print("Script executed successfully.")
        else:
            print("Script encountered an error.")
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)

    except subprocess.CalledProcessError as e:
        # Save the error output and script contents to a file named "error.txt" in the current directory
        error_file = "error.txt"
        with open(error_file, 'w') as file:
            file.write(f"Error Output:\n{e.stderr}\n\n")
            file.write(f"Script Contents:\n")
            with open(script_path, 'r') as script_file:
                script_contents = script_file.read()
                file.write(script_contents)

        print(f"Error occurred. Details saved to {error_file}.")

        # Save the error output and script contents to the project-specific error file
        project_folder = os.path.join("Projects", project_name)
        bug_fix_folder = os.path.join(project_folder, f"BugFix{bug_fix_counter}")
        os.makedirs(bug_fix_folder, exist_ok=True)

        project_error_file = os.path.join(bug_fix_folder, f"{project_name}error.txt")
        with open(project_error_file, 'w') as file:
            file.write(f"Error Output:\n{e.stderr}\n\n")
            file.write(f"Script Contents:\n")
            with open(script_path, 'r') as script_file:
                script_contents = script_file.read()
                file.write(script_contents)

        print(f"Error details saved to {project_error_file}.")

        # Run the AI Error Analysis
        user_input = f"Error Output:\n{e.stderr}\n\nScript Contents:\n{script_contents}"
        response = ollama_chat(user_input, system_message, args.model, conversation_history, args.output_file)
        print(NEON_GREEN + "AI Error Analysis Response:\n\n" + response + RESET_COLOR)

        # Save the raw AI output to the project folder
        ai_output_file = os.path.join(bug_fix_folder, "AIOutputRaw.txt")
        with open(ai_output_file, 'w') as file:
            file.write(response)

        # Extract the isolated fix and full new version from the AI output
        isolated_fix_match = re.search(r'Isolated Fix:\s*```python\s*(.*?)\s*```', response, re.DOTALL)
        full_new_version_match = re.search(r'New:\s*```python\s*(.*?)\s*```', response, re.DOTALL)

        if isolated_fix_match and full_new_version_match:
            isolated_fix = isolated_fix_match.group(1)
            full_new_version = full_new_version_match.group(1)

            # Save the isolated fix to a file
            fix_file = os.path.join(bug_fix_folder, f"{project_name}fix.txt")
            with open(fix_file, 'w') as file:
                file.write(isolated_fix)

           # Save the full new version to a Python file
            fix_script_file = os.path.join(bug_fix_folder, f"{project_name}Fix{bug_fix_counter}.py")
            with open(fix_script_file, 'w') as file:
                file.write(full_new_version)

            print(f"Isolated fix saved to {fix_file}")
            print(f"Full new version saved to {fix_script_file}")
        else:
            print("Isolated fix or full new version not found in the AI output.")
            print("Saving the entire AI response as the fix.")

            # Save the entire AI response as the fix
            fix_file = os.path.join(bug_fix_folder, f"{project_name}fix.txt")
            with open(fix_file, 'w') as file:
                file.write(response)

            print(f"AI response saved as the fix to {fix_file}")
        
            # Set fix_script_file to None since it's not created in this case
            fix_script_file = None

        print(f"AI response saved as the {project_name}Fix{bug_fix_counter}.py file to {fix_script_file}")

        # Create a "LabTest" folder and copy the backed-up environment and the new NameFixN.py file
        lab_test_folder = os.path.join(project_folder, "LabTest")
        os.makedirs(lab_test_folder, exist_ok=True)

        # Copy the backed-up environment to the "LabTest" folder
        backup_env_folder = os.path.join(project_folder, f"{os.path.basename(project_folder)}BACKUPS", f"{bug_fix_counter}-{bug_fix_counter}", f"{os.path.basename(project_folder)}CURRENV")
        for file_name in os.listdir(backup_env_folder):
            file_path = os.path.join(backup_env_folder, file_name)
            if os.path.isfile(file_path):
                shutil.copy2(file_path, os.path.join(lab_test_folder, file_name))

        # Copy the new NameFixN.py file to the "LabTest" folder if it exists
        if fix_script_file:
            shutil.copy2(fix_script_file, os.path.join(lab_test_folder, f"{project_name}Fix{bug_fix_counter}.py"))

            # Run the NameFixN.py file in the "LabTest" folder
            lab_test_script_path = os.path.join(lab_test_folder, f"{project_name}Fix{bug_fix_counter}.py")
            try:
                subprocess.run(['python3', lab_test_script_path], check=True)
                print(f"Successfully executed {project_name}Fix{bug_fix_counter}.py in the LabTest environment.")

                # Prompt the user to overwrite the previous file in the working directory
                overwrite = input("The script executed successfully in the lab environment. Do you want to overwrite the previous file in your working directory? (yes/no): ")
                if overwrite.lower() == "yes":
                    original_script_path = os.path.join(os.path.dirname(script_path), f"{project_name}.py")
                    shutil.copy2(lab_test_script_path, original_script_path)
                    print(f"Overwritten the previous file in the working directory with {project_name}Fix{bug_fix_counter}.py.")
                else:
                    print("Skipped overwriting the previous file in the working directory.")

            except subprocess.CalledProcessError:
                print(f"Error occurred while executing {project_name}Fix{bug_fix_counter}.py in the LabTest environment.")
        else:
            print(f"No {project_name}Fix{bug_fix_counter}.py file found. Skipping LabTest execution.")

        # Increment the bug fix counter
        bug_fix_counter += 1

    except Exception as e:
        print(f"An error occurred: {str(e)}")

    return bug_fix_counter

def select_script_file():
    # Open a file dialog to select the Python script file
    script_path = filedialog.askopenfilename(filetypes=[("Python Files", "*.py")])
    if script_path:
        # Get the project name from the script file name
        project_name = os.path.splitext(os.path.basename(script_path))[0]
        
        # Check if the project folder already exists
        project_folder = os.path.join("Projects", project_name)
        if not os.path.exists(project_folder):
            os.makedirs(project_folder)
            print(f"Created project folder: {project_folder}")
        else:
            print(f"Project folder already exists: {project_folder}")

        # Initialize the bug fix counter
        bug_fix_counter = 1

        # Run the selected script with the project name and bug fix counter
        bug_fix_counter = run_script(script_path, project_name, bug_fix_counter)
    else:
        print("No file selected.")

# Check if the "Projects" folder exists, create it if not
if not os.path.exists("Projects"):
    os.makedirs("Projects")
    print("Created 'Projects' folder.")

# Parse command-line arguments
parser = argparse.ArgumentParser(description="AI Error Analysis")
parser.add_argument("--model", default="llama3", help="Ollama model to use (default: llama3)")
parser.add_argument("--output_file", default="output.txt", help="Path to the output file to save the responses (default: output.txt)")
args = parser.parse_args()

# Configuration for the Ollama API client
client = OpenAI(
    base_url='http://localhost:11434/v1',
    api_key='llama3'
)

# Conversation loop
conversation_history = []
system_message = "You are a helpful assistant that is an expert at fixing errors in code. You will be given an error message and the full code it originated from. Your task is to: 1 Provide a brief explanation of the error. 2. Write the isolated fix for the error in the following format: Isolated Fix: ```<isolated fix code>``` Write the full new version of the code in the following format: New: ``` <full new version of the code> ```. Please ensure that you use the exact formats Isolated Fix: and New: followed by the code snippets enclosed in python  delimiters. This is crucial for the response to be processed correctly by the automated system."

# Create the main window
root = tk.Tk()
root.title("Select Python Script")

# Create a button to open the file dialog
select_button = tk.Button(root, text="Select Script", command=select_script_file)
select_button.pack(pady=10)

# Run the main event loop
root.mainloop()
