"""Starting policy: least recently used eviction."""

from collections import OrderedDict


class Policy:
    def __init__(self, capacity):
        self.order = OrderedDict()
        self.capacity = capacity

    def access(self, key, cache):
        self.order.pop(key, None)
        victim = None
        if key not in cache and len(cache) >= self.capacity:
            victim = next(iter(self.order))
            self.order.pop(victim)
        self.order[key] = None
        return victim
