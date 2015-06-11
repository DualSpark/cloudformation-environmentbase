from childtemplatebase import ChildTemplateBase

class TemplateBase(ChildTemplateBase):
    '''
    [DEPRECATED] Backwards compatibility class wrapper to retain existing naming.
    This will be removed in a future release.
    '''
    def __init__(self, arg_dict):
        ChildTemplateBase.__init__(self,arg_dict)
