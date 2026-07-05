__version__ = "0.1.0"



def create_reasoner(ontology):
    """
    Create a reasoner instance.
    """
    from pyhornedowl.reasoning import create_reasoner
    from os import path, listdir
    
    dir = path.join(path.dirname(__file__), "pykonclude")
    libname = [f for f in listdir(dir) if any(f.endswith(s) for s in [".so", ".dylib", ".dll"])][0]
    libpath = path.abspath(path.join(dir, libname))
    

    return create_reasoner(libpath, ontology)