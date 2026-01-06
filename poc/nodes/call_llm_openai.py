from datetime import datetime
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import GraphState, Fact, Decision
import os

def _load_env():
    """Load .env file if python-dotenv is available."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # dotenv not installed, use system environment

def call_llm_openai(state: GraphState) -> GraphState:
    """
    Call OpenAI API to generate response.
    
    Requires OPENAI_API_KEY environment variable.
    """
    
    # Check if we should actually call LLM
    needs_llm = any(d.description == "needs_llm" for d in state.decisions)
    if not needs_llm:
        # Skip if not needed
        return state
    
    input_facts = [f for f in state.facts if f.key == "user_input"]
    if not input_facts:
        return state
    
    user_input = input_facts[0].value
    
    # Load environment variables
    _load_env()
    
    # Get API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Fallback to mock if no API key
        print("⚠️  OPENAI_API_KEY not found, using mock response")
        mock_response = f"[Mock LLM Response] This would explain: {user_input}"
        state = state.with_fact(Fact(
            key="llm_response",
            value=mock_response,
            timestamp=datetime.utcnow()
        ))
        return state
    
    try:
        from openai import OpenAI
        
        client = OpenAI(api_key=api_key)
        
        # Make API call
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that provides clear, concise explanations."},
                {"role": "user", "content": user_input}
            ],
            max_tokens=150,
            temperature=0.7
        )
        
        llm_response = response.choices[0].message.content
        
        # Record that we called the LLM
        state = state.with_decision(Decision(
            description="called_llm_openai",
            timestamp=datetime.utcnow()
        ))
        
        # Store the response
        state = state.with_fact(Fact(
            key="llm_response",
            value=llm_response,
            timestamp=datetime.utcnow()
        ))
        
        # Record model used
        state = state.with_fact(Fact(
            key="llm_model",
            value="gpt-4o-mini",
            timestamp=datetime.utcnow()
        ))
        
    except ImportError:
        print("⚠️  OpenAI library not installed. Install with: pip install openai")
        print("   Using mock response instead.")
        mock_response = f"[Mock LLM Response] This would explain: {user_input}"
        state = state.with_fact(Fact(
            key="llm_response",
            value=mock_response,
            timestamp=datetime.utcnow()
        ))
    except Exception as e:
        print(f"⚠️  OpenAI API error: {e}")
        print("   Using mock response instead.")
        mock_response = f"[Mock LLM Response] This would explain: {user_input}"
        state = state.with_fact(Fact(
            key="llm_response",
            value=mock_response,
            timestamp=datetime.utcnow()
        ))
    
    return state
