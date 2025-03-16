# plugins/registry.py
import importlib
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Dict, List, Optional, Type, Set

from plugins.base import Plugin, PluginCategory

logger = logging.getLogger("manusprime.registry")

class PluginRegistry:
    """Registry for managing and loading plugins."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PluginRegistry, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            # Dictionary of registered plugin classes by name
            self.plugin_classes: Dict[str, Type[Plugin]] = {}
            
            # Dictionary of active plugin instances by category
            self.active_plugins: Dict[PluginCategory, Plugin] = {}
            
            # Dictionary of all plugin instances by name
            self.plugin_instances: Dict[str, Plugin] = {}
            
            # Set of loaded module paths to prevent duplicate loading
            self.loaded_modules: Set[str] = set()
            
            # Set to track class paths to prevent duplicate registration
            self.registered_class_paths: Set[str] = set()
            
            self._initialized = True
    
    def register_plugin_class(self, plugin_class: Type[Plugin]) -> bool:
        """Register a plugin class.
        
        Args:
            plugin_class: The plugin class to register
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        if not issubclass(plugin_class, Plugin):
            logger.error(f"Cannot register {plugin_class.__name__}: Not a subclass of Plugin")
            return False
            
        if not hasattr(plugin_class, 'name') or not plugin_class.name:
            logger.error(f"Cannot register plugin class: Missing name attribute")
            return False
            
        if not hasattr(plugin_class, 'category') or not plugin_class.category:
            logger.error(f"Cannot register plugin {plugin_class.name}: Missing category attribute")
            return False
        
        # Skip registering the base Plugin class and its direct abstract subclasses
        if plugin_class.__name__ == 'Plugin' or (
            inspect.isabstract(plugin_class) and 
            Plugin in plugin_class.__bases__ and
            plugin_class.__module__ == 'plugins.base'
        ):
            return False
        
        # Create a unique identifier for this class to prevent duplicates
        class_path = f"{plugin_class.__module__}.{plugin_class.__name__}"
        if class_path in self.registered_class_paths:
            # Already registered this exact class, skip
            return False
            
        plugin_name = plugin_class.name
        
        if plugin_name in self.plugin_classes:
            # Only warn if it's a different class with the same name
            existing_class_path = f"{self.plugin_classes[plugin_name].__module__}.{self.plugin_classes[plugin_name].__name__}"
            if existing_class_path != class_path:
                logger.warning(f"Plugin name '{plugin_name}' already registered by {existing_class_path}. Will be overwritten by {class_path}")
        
        # Add to tracking sets and dictionaries
        self.registered_class_paths.add(class_path)
        self.plugin_classes[plugin_name] = plugin_class
        logger.debug(f"Registered plugin class: {plugin_name} ({plugin_class.category.value})")
        
        return True
    
    async def activate_plugin(self, plugin_name: str, config: Optional[Dict] = None) -> Optional[Plugin]:
        """Activate a plugin by name.
        
        Args:
            plugin_name: The name of the plugin to activate
            config: Configuration for the plugin
            
        Returns:
            Optional[Plugin]: The activated plugin instance or None if activation failed
        """
        if plugin_name not in self.plugin_classes:
            logger.error(f"Cannot activate plugin '{plugin_name}': Not registered")
            return None
        
        plugin_class = self.plugin_classes[plugin_name]
        
        try:
            # Create plugin instance
            plugin_instance = plugin_class(config)
            
            # Initialize the plugin
            success = await plugin_instance.initialize()
            if not success:
                logger.error(f"Failed to initialize plugin '{plugin_name}'")
                return None
                
            plugin_instance.initialized = True
            
            # Store in instances dictionary
            self.plugin_instances[plugin_name] = plugin_instance
            
            # If we already have an active plugin in this category, deactivate it
            category = plugin_class.category
            if category in self.active_plugins:
                old_plugin = self.active_plugins[category]
                logger.info(f"Replacing active plugin '{old_plugin.name}' in category '{category.value}'")
                await old_plugin.cleanup()
            
            # Set as active for this category
            self.active_plugins[category] = plugin_instance
            
            logger.info(f"Activated plugin: {plugin_name} ({category.value})")
            return plugin_instance
            
        except Exception as e:
            logger.error(f"Error activating plugin '{plugin_name}': {str(e)}")
            return None
    
    def discover_plugins(self, plugin_dir: str = "plugins") -> int:
        """Discover plugins from the specified directory."""
        import os
        import sys
        import importlib.util

        plugin_path = Path(plugin_dir)
        if not plugin_path.exists() or not plugin_path.is_dir():
            logger.error(f"Plugin directory not found: {plugin_dir}")
            return 0
        
        initial_count = len(self.plugin_classes)
        processed_files = set()  # Track processed files to avoid duplicates
        
        # Get all Python files recursively in the plugin directory
        for root, _, files in os.walk(str(plugin_path)):
            for file in files:
                if file.endswith('.py') and file != '__init__.py':
                    # Get the full file path
                    file_path = os.path.join(root, file)
                    
                    # Skip if we've already processed this file
                    if file_path in processed_files:
                        continue
                    processed_files.add(file_path)
                    
                    # Convert file path to module path
                    rel_path = os.path.relpath(file_path, os.path.dirname(plugin_path))
                    module_name = os.path.splitext(rel_path.replace(os.sep, '.'))[0]
                    
                    if module_name in self.loaded_modules:
                        continue
                    
                    try:
                        # Import the module from file path
                        spec = importlib.util.spec_from_file_location(module_name, file_path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        self.loaded_modules.add(module_name)
                        
                        # Find plugin classes in the module
                        for name, obj in inspect.getmembers(module):
                            if (inspect.isclass(obj) and 
                                issubclass(obj, Plugin) and 
                                obj is not Plugin and
                                not inspect.isabstract(obj)):
                                self.register_plugin_class(obj)
                        
                    except Exception as e:
                        logger.warning(f"Error loading module {module_name} from {file_path}: {e}")
        
        new_plugins = len(self.plugin_classes) - initial_count
        logger.info(f"Discovered {new_plugins} new plugin classes from {plugin_dir}")
        
        return new_plugins
    
    def get_plugin(self, name: str) -> Optional[Plugin]:
        """Get a plugin instance by name.
        
        Args:
            name: The name of the plugin
            
        Returns:
            Optional[Plugin]: The plugin instance or None if not found
        """
        return self.plugin_instances.get(name)
    
    def get_active_plugin(self, category: PluginCategory) -> Optional[Plugin]:
        """Get the active plugin for a category.
        
        Args:
            category: The plugin category
            
        Returns:
            Optional[Plugin]: The active plugin for the category or None
        """
        return self.active_plugins.get(category)
    
    def get_plugin_classes_by_category(self, category: PluginCategory) -> List[Type[Plugin]]:
        """Get all plugin classes for a specific category.
        
        Args:
            category: The plugin category
            
        Returns:
            List[Type[Plugin]]: List of plugin classes in the category
        """
        return [cls for cls in self.plugin_classes.values() if cls.category == category]
    
    async def cleanup_all(self) -> None:
        """Clean up all active plugins."""
        plugins_to_cleanup = list(self.plugin_instances.values())
        
        for plugin in plugins_to_cleanup:
            try:
                await plugin.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up plugin '{plugin.name}': {str(e)}")
        
        self.active_plugins.clear()
        self.plugin_instances.clear()


# Create a global instance
registry = PluginRegistry()