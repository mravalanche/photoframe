import unittest

class DummyApp:
    config = dict()


class EnvTest(unittest.TestCase):
    def test_env(self):
        from config import load_config
        
        app = DummyApp()
        load_config(app)

        self.assertEqual(app.config["EXAMPLE_ENV"], "example-env")
        print()

    

if __name__ == '__main__':
    unittest.main()
